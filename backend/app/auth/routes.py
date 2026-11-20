"""POST /api/auth/login, /api/auth/demo, /api/auth/totp, /api/auth/logout, /api/auth/logout-all and
GET /api/me (contract §11.1, §11.2, T1-35). The users routes are in user_admin.py.

Login takes { "email", "password" } and answers as GET /api/me does, setting the session cookie
(HttpOnly, Secure, SameSite=Lax, path /api). A wrong email and a wrong password get the same 401,
in about the same time. GET /api/me gives the signed-in user, the CSRF token that every request
other than a GET must send as X-CSRF-Token, and `step`: what sign-in still needs.

Two-factor (§11.1): an Owner signs in with the password, then an authenticator code. step says
which: TOTP (send the code to POST /api/auth/totp) or TOTP_SETUP (an Owner without two-factor:
POST /api/auth/totp with no code gives the secret for the app, then with a code turns it on).
Until step is DONE, the session reaches nothing else (access.gate). Each success starts a new
session, so a cookie from before the code is worth nothing after it.

POST /api/auth/demo { "role" } signs in as that role's demo user from users.json, on demo
playground sites only (demo_login).

Rate limit (§11.1, 429): after LIMIT_TRIES wrong passwords for one email, or wrong codes for one
user, in LIMIT_WINDOW, sign-in answers 429 RATE_LIMITED with Retry-After until the oldest of them
is LIMIT_WINDOW old, the right password or code included. The tries are the audit log's rows.
"""

import math
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, Request, Response
from pydantic import Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..api.pack import read_farm
from ..api.routes import EngineDep, PackDep, SettingsDep, SiteDep, _database, _errors
from ..api.schemas import ApiTime
from ..contract import ContractModel
from ..db.models import AuditLog, User, UserSession
from ..errors import ApiError
from . import audit, totp
from .access import OWNER, Caller, SignedIn
from .passwords import check_password
from .sessions import COOKIE, COOKIE_PATH, FULL, TOTP, TOTP_SETUP, csrf_token, new_token

WRONG = "Wrong email or password"
USERS = User.__table__  # rows by column name, on a plain connection
MAX_WRONG_CODES = 5  # then the session ends and sign-in starts again with the password
STEPS = {FULL: "DONE", TOTP: "TOTP", TOTP_SETUP: "TOTP_SETUP"}
LIMIT_TRIES = 5  # wrong passwords per email, or wrong codes per user, in LIMIT_WINDOW (§11.1, 429)
LIMIT_WINDOW = timedelta(minutes=15)


def _refuse_if_limited(connection, site, key: str, action: str, *which) -> None:
    """429 RATE_LIMITED once LIMIT_TRIES `action` rows (`which`) are in the audit log from the last
    LIMIT_WINDOW. The lock on `key` holds other tries for it until this one is counted and saved, so
    tries sent at once can't get past the limit together."""
    connection.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"{site.org_id}|{site.site_id}|{key}"))))
    now = datetime.now(UTC)
    tries = connection.scalars(
        select(AuditLog.at).where(
            AuditLog.org_id == site.org_id, AuditLog.site_id == site.site_id, AuditLog.action == action,
            AuditLog.at > now - LIMIT_WINDOW, *which,
        ).order_by(AuditLog.at.desc()).limit(LIMIT_TRIES)
    ).all()
    if len(tries) < LIMIT_TRIES:
        return
    wait = max(1, math.ceil((tries[-1] + LIMIT_WINDOW - now).total_seconds()))
    connection.rollback()  # lets the lock go
    raise ApiError(429, "RATE_LIMITED", f"Too many wrong tries: wait {math.ceil(wait / 60)} minutes and try again",
                   headers={"Retry-After": str(wait)})


class LoginBody(ContractModel):
    email: Annotated[str, Field(min_length=3, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]  # scrypt on a huge one is slow


class MeUser(ContractModel):
    id: str
    name: str
    email: str
    role: str  # OWNER, AGRONOMIST, OPERATOR or VIEWER (§13)


class Me(ContractModel):
    """GET /api/me: who is signed in, the CSRF token for every request that isn't a GET, and what
    sign-in still needs: DONE, TOTP (the authenticator code) or TOTP_SETUP (set up two-factor)."""

    user: MeUser
    csrf_token: str
    expires_at: ApiTime  # when the session ends; sign in again after that
    step: Literal["DONE", "TOTP", "TOTP_SETUP"]


class TotpBody(ContractModel):
    code: Annotated[str | None, Field(max_length=12)] = None


class TotpSetup(ContractModel):
    """The new two-factor secret: for the authenticator app, as a QR code of otpauthUri or typed in."""

    secret: str
    otpauth_uri: str


def _me(settings, caller: Caller) -> Me:
    return Me(
        user=MeUser(id=caller.user_id, name=caller.name, email=caller.email, role=caller.role),
        csrf_token=csrf_token(settings.session_secret, caller.token),
        expires_at=caller.token.expires_at,
        step=STEPS[caller.stage],
    )


def _stage_after_password(user) -> str:
    if user.totp_enabled_at is not None:
        return TOTP
    return TOTP_SETUP if user.role == OWNER else FULL


def login(body: LoginBody, response: Response, settings: SettingsDep, engine: EngineDep, site: SiteDep) -> Me:
    """Sign in. 401 for a wrong email or password, or a turned-off user, the same for all three.
    429 after too many of those for this email (_refuse_if_limited)."""
    email = body.email.strip().lower()
    with _database(engine) as connection:
        _refuse_if_limited(connection, site, f"email|{email}", "SIGN_IN_FAILED", AuditLog.detail["email"].astext == email)
        user = connection.execute(
            select(USERS).where(User.org_id == site.org_id, User.site_id == site.site_id, func.lower(User.email) == email)
        ).first()
        # The password is checked even when there is no such user, so both take the same time.
        if not check_password(body.password, user.password_hash if user else None) or user is None \
                or user.disabled_at is not None:
            detail = {"email": email} | ({"turnedOff": True} if user is not None and user.disabled_at else {})
            audit.write(connection, site, user.user_id if user else None, user.role if user else None,
                        "SIGN_IN_FAILED", detail=detail)
            connection.commit()
            raise ApiError(401, "UNAUTHORIZED", WRONG)
        stage = _stage_after_password(user)
        return _start_session(connection, response, settings, site, user, stage,
                              "SIGN_IN" if stage == FULL else "SIGN_IN_PASSWORD")


def _start_session(connection, response: Response, settings, site, user, stage: str, action: str) -> Me:
    """A new session for `user`: its row, its audit row (`action`), the cookie, and the /me answer."""
    now = datetime.now(UTC)
    token, cookie = new_token(settings.session_secret, user.user_id, now, settings.session_hours, stage)
    connection.execute(
        insert(UserSession).values(
            org_id=site.org_id, site_id=site.site_id, session_hash=token.session_hash,
            user_id=user.user_id, created_at=now, expires_at=token.expires_at, stage=stage, failed_codes=0,
        )
    )
    audit.write(connection, site, user.user_id, user.role, action)
    connection.commit()
    response.set_cookie(
        COOKIE, cookie, max_age=int((token.expires_at - now).total_seconds()), path=COOKIE_PATH,
        secure=True, httponly=True, samesite="lax",
    )
    return _me(settings, Caller(user.user_id, user.name, user.email, user.role, token, stage))


def _this_session(site, caller: Caller):
    return (UserSession.org_id == site.org_id, UserSession.site_id == site.site_id,
            UserSession.session_hash == caller.token.session_hash)


def two_factor(
    body: TotpBody, response: Response, settings: SettingsDep, engine: EngineDep, site: SiteDep, caller: SignedIn
) -> Me | TotpSetup:
    """The authenticator step of signing in (§11.1). With step TOTP, the code: a new, full session.
    With step TOTP_SETUP, no code: a new secret for the app; then the code from the app: two-factor
    is on, and a new, full session. A wrong code is 400 WRONG_CODE; the fifth ends the session (401).
    Too many wrong codes for this user, over every session, is 429 (_refuse_if_limited)."""
    if caller.stage == FULL:
        raise ApiError(409, "CONFLICT", "Two-factor is already done for this session")
    now = datetime.now(UTC)
    the_user = (User.org_id == site.org_id, User.site_id == site.site_id, User.user_id == caller.user_id)
    with _database(engine) as connection:
        user = connection.execute(select(USERS).where(*the_user)).first()
        if caller.stage == TOTP_SETUP and body.code is None:
            secret = totp.new_secret()
            connection.execute(update(User).where(*the_user).values(totp_secret=secret))
            connection.commit()
            return TotpSetup(secret=secret, otpauth_uri=totp.otpauth_uri(secret, user.email))
        if body.code is None:
            raise ApiError(422, "VALIDATION_ERROR", "Send the 6-digit code from the authenticator app")
        if user.totp_secret is None:
            raise ApiError(409, "CONFLICT", "Ask for the two-factor secret first: POST /api/auth/totp with no code")
        _refuse_if_limited(connection, site, f"user|{caller.user_id}", "TOTP_FAILED", AuditLog.user_id == caller.user_id)
        last = user.totp_last_step if caller.stage == TOTP else None
        step = totp.matching_step(user.totp_secret, body.code, now, last)
        if step is None:
            failed = connection.scalar(
                update(UserSession).where(*_this_session(site, caller))
                .values(failed_codes=UserSession.failed_codes + 1).returning(UserSession.failed_codes)
            )
            audit.write(connection, site, caller.user_id, caller.role, "TOTP_FAILED", detail={"failedCodes": failed})
            if failed >= MAX_WRONG_CODES:
                connection.execute(update(UserSession).where(*_this_session(site, caller)).values(ended_at=now))
                connection.commit()
                response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax")
                raise ApiError(401, "UNAUTHORIZED", "Too many wrong codes: sign in again")
            connection.commit()
            raise ApiError(400, "WRONG_CODE", "That code is not right. Check the time on the phone and try again")
        changes = {"totp_last_step": step}
        if caller.stage == TOTP_SETUP:
            changes["totp_enabled_at"] = now
            audit.write(connection, site, caller.user_id, caller.role, "TOTP_ENABLED")
        connection.execute(update(User).where(*the_user).values(**changes))
        connection.execute(update(UserSession).where(*_this_session(site, caller)).values(ended_at=now))
        return _start_session(connection, response, settings, site, user, FULL, "SIGN_IN")


class DemoBody(ContractModel):
    role: Literal["OWNER", "AGRONOMIST", "OPERATOR", "VIEWER"]


def demo_login(
    body: DemoBody, response: Response, settings: SettingsDep, engine: EngineDep, site: SiteDep, pack: PackDep
) -> Me:
    """Demo sign-in: the demo user of `role` from users.json, with no password and no two-factor.
    Only on a site whose features.json has both "demoFarm" and "playground" on, so it can never act
    on a real farm; 404 anywhere else. The demo user is added to the database the first time,
    without a password, so it can't sign in through /api/auth/login. A turned-off one can't sign in."""
    features = read_farm(pack).features
    if features.get("demoFarm") is not True or features.get("playground") is not True:
        raise ApiError(404, "NOT_FOUND", "This site has no demo sign-in")
    try:
        users = pack.json("users.json")["users"]
        demo = next(u for u in users if u["role"] == body.role)
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise ApiError(404, "NOT_FOUND", f"users.json has no demo {body.role.title()}") from exc
    row = {"org_id": site.org_id, "site_id": site.site_id, "user_id": demo["id"], "name": demo["name"],
           "email": demo["email"].lower(), "role": body.role}
    with _database(engine) as connection:
        connection.execute(
            pg_insert(User).values(row).on_conflict_do_update(
                index_elements=["org_id", "site_id", "user_id"], set_={"name": row["name"], "role": row["role"]}
            )
        )
        user = connection.execute(
            select(USERS).where(User.org_id == site.org_id, User.site_id == site.site_id, User.user_id == demo["id"])
        ).first()
        if user.disabled_at is not None:
            raise ApiError(401, "UNAUTHORIZED", "This demo user is turned off")
        return _start_session(connection, response, settings, site, user, FULL, "SIGN_IN_DEMO")


def _end_sessions(request: Request, response: Response, engine, site, caller: Caller, every: bool) -> Response:
    which = [UserSession.org_id == site.org_id, UserSession.site_id == site.site_id, UserSession.ended_at.is_(None)]
    which.append(UserSession.user_id == caller.user_id if every else UserSession.session_hash == caller.token.session_hash)
    with _database(engine) as connection:
        connection.execute(update(UserSession).where(*which).values(ended_at=datetime.now(UTC)))
        audit.write(connection, site, caller.user_id, caller.role, "SIGN_OUT_ALL" if every else "SIGN_OUT")
        connection.commit()
    request.state.caller = None
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=True, httponly=True, samesite="lax")
    response.status_code = 204
    return response


def logout(request: Request, response: Response, engine: EngineDep, site: SiteDep, caller: SignedIn) -> Response:
    """Sign this browser out."""
    return _end_sessions(request, response, engine, site, caller, every=False)


def logout_all(request: Request, response: Response, engine: EngineDep, site: SiteDep, caller: SignedIn) -> Response:
    """Sign this user out everywhere, for example after a lost phone."""
    return _end_sessions(request, response, engine, site, caller, every=True)


def me(settings: SettingsDep, caller: SignedIn) -> Me:
    """The signed-in user, the CSRF token and what sign-in still needs."""
    return _me(settings, caller)


def add_auth_api(app: FastAPI) -> None:
    from .user_admin import add_user_api  # the users routes (Owner only)

    app.add_api_route(
        "/api/auth/login", login, methods=["POST"], response_model=Me, responses=_errors(401, 422, 429, 503),
        tags=["auth"],
    )
    app.add_api_route(
        "/api/auth/demo", demo_login, methods=["POST"], response_model=Me, responses=_errors(401, 404, 422, 503),
        tags=["auth"],
    )
    app.add_api_route(
        "/api/auth/totp", two_factor, methods=["POST"], response_model=Me | TotpSetup,
        responses=_errors(400, 401, 403, 409, 422, 429, 503), tags=["auth"],
    )
    for path, endpoint in (("/api/auth/logout", logout), ("/api/auth/logout-all", logout_all)):
        app.add_api_route(
            path, endpoint, methods=["POST"], status_code=204, response_class=Response,
            responses=_errors(401, 403, 503), tags=["auth"],
        )
    app.add_api_route("/api/me", me, response_model=Me, responses=_errors(401, 503), tags=["auth"])
    add_user_api(app)
