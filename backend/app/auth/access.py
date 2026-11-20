"""Who is calling, and may they (contract §11.1, §11.2, §13, T1-35).

`gate` runs before every /api route (main.py adds it to the whole app), so a new route needs a
signed-in user without anyone remembering to ask for one:

- /api/health and /api/auth/login are open to everyone (§11.2);
- POST /api/stop checks its session itself, with no database (stop_caller, sessions.py);
- everything else needs a session whose signature, expiry and database row are all good, of a
  user who isn't turned off: 401 otherwise. Every request that isn't a GET also needs the
  session's X-CSRF-Token: 403 otherwise;
- a session waiting for two-factor (stage TOTP or TOTP_SETUP) reaches only POST /api/auth/totp,
  GET /api/me and POST /api/auth/logout: 403 TWO_FACTOR_REQUIRED anywhere else, Stop included.

Routes that change the farm then ask for a role with `allowed(...)`: 403 for any other role. The
roles are §13's. The app shows locked buttons, but this is what enforces them.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select

from ..api.pack import site_of
from ..api.routes import EngineDep, SettingsDep, SiteDep, _database, get_pack
from ..db.models import User, UserSession
from ..errors import ApiError
from .sessions import COOKIE, CSRF_HEADER, FULL, Token, csrf_ok, read_token

OWNER, AGRONOMIST, OPERATOR, VIEWER = "OWNER", "AGRONOMIST", "OPERATOR", "VIEWER"
ROLES = (OWNER, AGRONOMIST, OPERATOR, VIEWER)
STAFF = (OWNER, AGRONOMIST, OPERATOR)  # everyone but a Viewer

OPEN = {"/api/health", "/api/auth/login", "/api/auth/demo"}  # demo: demo playground sites only
BEFORE_TWO_FACTOR = {"/api/auth/totp", "/api/me", "/api/auth/logout"}  # all a TOTP/TOTP_SETUP session may do
STOP = "/api/stop"
READ_ONLY = {"GET", "HEAD", "OPTIONS"}

NOT_SIGNED_IN = "Sign in first"
TWO_FACTOR_FIRST = "Finish signing in with your authenticator app first"
NO_CSRF = f"The {CSRF_HEADER} header is missing or wrong. GET /api/me gives it"


@dataclass(frozen=True)
class Caller:
    """The signed-in person behind a request."""

    user_id: str
    name: str
    email: str
    role: str
    token: Token
    stage: str = FULL  # FULL, or TOTP / TOTP_SETUP while two-factor isn't done (sessions.py)

    @property
    def issued_by(self) -> str:
        """issuedBy in the commands it sends (§7.1): "user:<id>", for the audit trail only."""
        return f"user:{self.user_id}"


def _not_signed_in() -> ApiError:
    return ApiError(401, "UNAUTHORIZED", NOT_SIGNED_IN)


def signed_in(request: Request, settings: SettingsDep, engine: EngineDep, site: SiteDep) -> Caller:
    """The caller, from a session that is signed, unexpired and not signed out. 401 otherwise;
    503 when the database can't answer."""
    known = getattr(request.state, "caller", None)
    if known is not None:
        return known
    now = datetime.now(UTC)
    token = read_token(settings.session_secret, request.cookies.get(COOKIE), now)
    if token is None:
        raise _not_signed_in()
    with _database(engine) as connection:
        row = connection.execute(
            select(User.user_id, User.name, User.email, User.role, UserSession.stage)
            .join(
                UserSession,
                (UserSession.org_id == User.org_id)
                & (UserSession.site_id == User.site_id)
                & (UserSession.user_id == User.user_id),
            )
            .where(
                UserSession.org_id == site.org_id,
                UserSession.site_id == site.site_id,
                UserSession.session_hash == token.session_hash,
                UserSession.user_id == token.user_id,
                UserSession.ended_at.is_(None),
                UserSession.expires_at > now,
                User.disabled_at.is_(None),
            )
        ).first()
    if row is None:
        raise _not_signed_in()
    caller = Caller(row.user_id, row.name, row.email, row.role, token, row.stage)
    request.state.caller = caller
    return caller


SignedIn = Annotated[Caller, Depends(signed_in)]


def _check_csrf(request: Request, secret: str, token: Token) -> None:
    if request.method not in READ_ONLY and not csrf_ok(secret, token, request.headers.get(CSRF_HEADER)):
        raise ApiError(403, "FORBIDDEN", NO_CSRF)


def gate(request: Request, settings: SettingsDep, engine: EngineDep) -> None:
    """Runs before every route: see the module's docstring. The site is read only past the open
    routes, so /api/health still needs neither the config pack nor the database."""
    path = request.url.path
    if path in OPEN or path == STOP:
        return
    caller = signed_in(request, settings, engine, site_of(get_pack(settings, engine)))
    if caller.stage != FULL and path not in BEFORE_TWO_FACTOR:
        raise ApiError(403, "TWO_FACTOR_REQUIRED", TWO_FACTOR_FIRST)
    _check_csrf(request, settings.session_secret, caller.token)


def stop_caller(request: Request, settings: SettingsDep) -> Token:
    """POST /api/stop's check: a signed, unexpired session and its CSRF token, with no database, so
    Stop works while the database is down (§8.3 rule 9). Every role may Stop (§13)."""
    token = read_token(settings.session_secret, request.cookies.get(COOKIE), datetime.now(UTC))
    if token is None:
        raise _not_signed_in()
    if token.stage != FULL:
        raise ApiError(403, "TWO_FACTOR_REQUIRED", TWO_FACTOR_FIRST)
    _check_csrf(request, settings.session_secret, token)
    return token


StopCaller = Annotated[Token, Depends(stop_caller)]


def allowed(*roles: str, why: str):
    """A dependency that lets only these roles through: 403 with `why`, the one-line reason the app
    shows, for any other. Make each one once, at module level, as the routes do (may_dose …)."""

    def check(caller: SignedIn) -> Caller:
        if caller.role not in roles:
            raise ApiError(403, "FORBIDDEN", why)
        return caller

    return check
