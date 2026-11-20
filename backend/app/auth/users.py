"""Make the site's first Owner (T1-35).

Run from backend/, after `alembic upgrade head` and the seed (app.db.seed):
    python -m app.auth.users create-owner --email owner@example.org --name "Farm Owner"

On the staging server, inside the API container:
    docker compose --env-file infra/.env.staging -f infra/docker-compose.staging.yml \
        exec api python -m app.auth.users create-owner --email ... --name ...

It makes a random password, stores only its hash and prints the password once: put it in the
password manager, then sign in. It refuses when the site already has an Owner. The Owner then adds
everyone else in the app (GET/POST /api/users, contract §11.2).

A lost phone: `reset-two-factor --email ...` turns the user's two-factor off and signs them out
everywhere; an Owner sets it up again at the next sign-in.
"""

import argparse
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, func, insert, select, update

from ..api.pack import SiteRef, site_of
from ..config import load_settings
from ..db.engine import create_db_engine
from ..db.models import Site, User, UserSession
from ..packs import load_pack
from . import audit
from .access import OWNER
from .passwords import hash_password, new_password


class Refused(Exception):
    """The Owner was not made; the message says why."""


def create_owner(engine: Engine, site: SiteRef, email: str, name: str) -> tuple[str, str]:
    """Add the site's first Owner. Returns (user_id, password); the password is not stored."""
    email, name = email.strip().lower(), name.strip()
    if "@" not in email or not name:
        raise Refused("give an email address and a name")
    password = new_password()
    user_id = uuid.uuid4().hex
    with engine.begin() as connection:
        if connection.scalar(select(Site.site_id).where(Site.org_id == site.org_id, Site.site_id == site.site_id)) is None:
            raise Refused(f"site {site.org_id}/{site.site_id} isn't in the database: run python -m app.db.seed first")
        mine = (User.org_id == site.org_id, User.site_id == site.site_id)
        if connection.scalar(select(User.user_id).where(*mine, User.role == OWNER)) is not None:
            raise Refused("this site already has an Owner; more users come with user management")
        if connection.scalar(select(User.user_id).where(*mine, func.lower(User.email) == email)) is not None:
            raise Refused(f"{email} already has an account")
        connection.execute(
            insert(User).values(
                org_id=site.org_id, site_id=site.site_id, user_id=user_id, name=name, email=email,
                role=OWNER, password_hash=hash_password(password),
            )
        )
        audit.write(connection, site, None, None, "USER_CREATED", target=user_id,
                    detail={"email": email, "role": OWNER, "by": "python -m app.auth.users create-owner",
                            "at": datetime.now(UTC).isoformat()})
    return user_id, password


def reset_two_factor(engine: Engine, site: SiteRef, email: str) -> None:
    """Turn the user's two-factor off and end their sessions, for a lost or replaced phone."""
    email = email.strip().lower()
    mine = (User.org_id == site.org_id, User.site_id == site.site_id)
    with engine.begin() as connection:
        user_id = connection.scalar(select(User.user_id).where(*mine, func.lower(User.email) == email))
        if user_id is None:
            raise Refused(f"there is no user {email}")
        connection.execute(update(User).where(*mine, User.user_id == user_id).values(
            totp_secret=None, totp_enabled_at=None, totp_last_step=None))
        connection.execute(update(UserSession).where(
            UserSession.org_id == site.org_id, UserSession.site_id == site.site_id,
            UserSession.user_id == user_id, UserSession.ended_at.is_(None)).values(ended_at=datetime.now(UTC)))
        audit.write(connection, site, None, None, "TOTP_RESET", target=user_id,
                    detail={"email": email, "by": "python -m app.auth.users reset-two-factor"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make the site's first Owner.")
    commands = parser.add_subparsers(dest="command", required=True)
    owner = commands.add_parser("create-owner", help="add the first Owner and print its password once")
    owner.add_argument("--email", required=True)
    owner.add_argument("--name", required=True)
    reset = commands.add_parser("reset-two-factor", help="turn a user's two-factor off, after a lost phone")
    reset.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = create_db_engine(settings)
    try:
        site = site_of(load_pack(settings, engine))
        if args.command == "reset-two-factor":
            reset_two_factor(engine, site, args.email)
            print(f"Two-factor is off for {args.email.strip().lower()}; they set it up again at the next sign-in.")
            return 0
        _, password = create_owner(engine, site, args.email, args.name)
    except Refused as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"Owner {args.email.strip().lower()} made for {site.org_id}/{site.site_id}.")
    print()
    print("Password. Put it in the password manager now; it is not stored anywhere else:")
    print()
    print(f"    {password}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
