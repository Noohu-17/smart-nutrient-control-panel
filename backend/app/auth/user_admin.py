"""Users and roles, for the Owner (contract §11.2, §13, T1-35).

    GET   /api/users                  every user of the site, turned-off ones too
    POST  /api/users                  { name, email, role }: a new user, with no password yet: 201
    PATCH /api/users/{id}             { name?, role?, active? }: rename, change the role, turn off or on
    POST  /api/users/{id}/invite      a new random password, answered once, for the Owner to hand over

There is no email: the Owner gives the invite's password to the person, who signs in with it.
Changing someone's role or turning them off signs them out everywhere, and so does an invite; their
browsers get no more pushes either (T1-41).
The site always keeps one active Owner: the last one can't be turned off or given another role
(409 LAST_OWNER). Every change is in the audit log.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Path
from pydantic import Field
from sqlalchemy import func, insert, select, update

from ..api.routes import EngineDep, SiteDep, _database, _errors
from ..contract import ContractModel
from ..db.models import User, UserSession
from ..errors import ApiError
from ..push.rules import forget_user
from . import audit
from .access import OWNER, ROLES, Caller, allowed
from .passwords import hash_password, new_password

USERS = User.__table__
Role = Literal["OWNER", "AGRONOMIST", "OPERATOR", "VIEWER"]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Email = Annotated[str, Field(min_length=3, max_length=254, pattern=r"^\s*[^@\s]+@[^@\s]+\s*$")]  # trimmed later

may_manage = allowed(OWNER, why="Only an Owner manages users")  # §11.2
MayManage = Annotated[Caller, Depends(may_manage)]
UserId = Annotated[str, Path(alias="id", max_length=64)]


class UserInfo(ContractModel):
    id: str
    name: str
    email: str
    role: str
    active: bool  # false: turned off, can't sign in
    two_factor: bool  # an authenticator app is set up
    has_password: bool  # false until the first invite (and always for a demo user)


class UserList(ContractModel):
    items: list[UserInfo]


class NewUser(ContractModel):
    name: Name
    email: Email
    role: Role


class UserChange(ContractModel):
    name: Name | None = None
    role: Role | None = None
    active: bool | None = None


class Invite(ContractModel):
    """The new password, shown once. It isn't stored anywhere but as a hash."""

    user: UserInfo
    password: str


def _info(row) -> UserInfo:
    return UserInfo(
        id=row.user_id, name=row.name, email=row.email, role=row.role, active=row.disabled_at is None,
        two_factor=row.totp_enabled_at is not None, has_password=row.password_hash is not None,
    )


def _mine(site):
    return (User.org_id == site.org_id, User.site_id == site.site_id)


def _the_user(connection, site, user_id: str):
    row = connection.execute(select(USERS).where(*_mine(site), User.user_id == user_id)).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"There is no user {user_id}")
    return row


def _sign_out_everywhere(connection, site, user_id: str, now: datetime) -> None:
    """Every session ends, and no browser of the user gets pushes any more (T1-41)."""
    forget_user(connection, site.org_id, site.site_id, user_id)
    connection.execute(
        update(UserSession)
        .where(UserSession.org_id == site.org_id, UserSession.site_id == site.site_id,
               UserSession.user_id == user_id, UserSession.ended_at.is_(None))
        .values(ended_at=now)
    )


def list_users(engine: EngineDep, site: SiteDep, caller: MayManage) -> UserList:
    """Every user of the site, by name."""
    with _database(engine) as connection:
        rows = connection.execute(select(USERS).where(*_mine(site)).order_by(func.lower(User.name))).all()
    return UserList(items=[_info(row) for row in rows])


def create_user(body: NewUser, engine: EngineDep, site: SiteDep, caller: MayManage) -> UserInfo:
    """A new user with no password: POST /api/users/{id}/invite gives one. 409 if the email is taken."""
    email = body.email.strip().lower()
    with _database(engine) as connection:
        if connection.scalar(select(User.user_id).where(*_mine(site), func.lower(User.email) == email)) is not None:
            raise ApiError(409, "CONFLICT", f"{email} already has an account")
        user_id = uuid.uuid4().hex
        connection.execute(insert(User).values(
            org_id=site.org_id, site_id=site.site_id, user_id=user_id, name=body.name.strip(), email=email,
            role=body.role,
        ))
        audit.write(connection, site, caller.user_id, caller.role, "USER_CREATED", user_id,
                    {"email": email, "role": body.role})
        connection.commit()
        return _info(_the_user(connection, site, user_id))


def _active_owners(connection, site) -> int:
    return connection.scalar(
        select(func.count()).select_from(USERS).where(*_mine(site), User.role == OWNER, User.disabled_at.is_(None))
    )


def change_user(user_id: UserId, body: UserChange, engine: EngineDep, site: SiteDep, caller: MayManage) -> UserInfo:
    """Rename, change the role, or turn off (active: false) or on. A new role or turning off signs the
    user out everywhere. 409 LAST_OWNER for a change that would leave no active Owner."""
    now = datetime.now(UTC)
    with _database(engine) as connection:
        row = _the_user(connection, site, user_id)
        changes, detail = {}, {}
        if body.name is not None and body.name.strip() != row.name:
            changes["name"] = detail["name"] = body.name.strip()
        if body.role is not None and body.role != row.role:
            changes["role"] = detail["role"] = body.role
        if body.active is not None and body.active != (row.disabled_at is None):
            changes["disabled_at"] = None if body.active else now
            detail["active"] = body.active
        stays_owner = changes.get("role", row.role) == OWNER and changes.get("disabled_at", row.disabled_at) is None
        if row.role == OWNER and row.disabled_at is None and not stays_owner and _active_owners(connection, site) <= 1:
            raise ApiError(409, "LAST_OWNER", "The site needs an active Owner: make someone else Owner first")
        if changes:
            connection.execute(update(User).where(*_mine(site), User.user_id == user_id).values(**changes))
            if "role" in changes or "disabled_at" in changes:
                _sign_out_everywhere(connection, site, user_id, now)
            audit.write(connection, site, caller.user_id, caller.role, "USER_CHANGED", user_id, detail)
            connection.commit()
        return _info(_the_user(connection, site, user_id))


def invite_user(user_id: UserId, engine: EngineDep, site: SiteDep, caller: MayManage) -> Invite:
    """A new random password for the user, shown once, for the Owner to hand over. It replaces any
    old one and signs the user out everywhere. 409 for a turned-off user."""
    password = new_password()
    with _database(engine) as connection:
        row = _the_user(connection, site, user_id)
        if row.disabled_at is not None:
            raise ApiError(409, "CONFLICT", "Turn the user on first")
        connection.execute(
            update(User).where(*_mine(site), User.user_id == user_id).values(password_hash=hash_password(password))
        )
        _sign_out_everywhere(connection, site, user_id, datetime.now(UTC))
        audit.write(connection, site, caller.user_id, caller.role, "USER_INVITED", user_id)
        connection.commit()
        return Invite(user=_info(_the_user(connection, site, user_id)), password=password)


def add_user_api(app: FastAPI) -> None:
    assert set(ROLES) == set(Role.__args__)  # the roles here are §13's, as in access.py
    app.add_api_route("/api/users", list_users, response_model=UserList, responses=_errors(401, 403, 503),
                      tags=["users"])
    app.add_api_route("/api/users", create_user, methods=["POST"], status_code=201, response_model=UserInfo,
                      responses=_errors(401, 403, 409, 422, 503), tags=["users"])
    app.add_api_route("/api/users/{id}", change_user, methods=["PATCH"], response_model=UserInfo,
                      responses=_errors(401, 403, 404, 409, 422, 503), tags=["users"])
    app.add_api_route("/api/users/{id}/invite", invite_user, methods=["POST"], response_model=Invite,
                      responses=_errors(401, 403, 404, 409, 422, 503), tags=["users"])
