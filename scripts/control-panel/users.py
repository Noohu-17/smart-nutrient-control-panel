"""The control panel's sign-in accounts on the local back end (DEV ONLY).

Run inside the api container by scripts/control-panel/backend.mjs, after the seed:
    docker compose ... exec -T api python - < scripts/control-panel/users.py

Adds or updates the four accounts below on the client's site. The passwords are shorter than the
back end's 12-character minimum (app/auth/passwords.py), so the hash is made here the same way
hash_password makes it, without that check: fine for a laptop demo, never for a real farm.
Two-factor is the back end's own: the Owner sets up an authenticator app at the first sign-in.
"""

import base64
import secrets
import uuid

from sqlalchemy import func, insert, select, update

from app.api.pack import site_of
from app.auth.passwords import N, P, R, SALT_BYTES, _scrypt
from app.config import load_settings
from app.db.engine import create_db_engine
from app.db.models import User
from app.packs import load_pack

ACCOUNTS = [
    ("Jack", "jack123@gmail.com", "OWNER", "jk12345"),
    ("Agronomist", "agronomist123@gmail.com", "AGRONOMIST", "ag12345"),
    ("Operator", "operator123@gmail.com", "OPERATOR", "op12345"),
    ("Viewer", "viewer123@gmail.com", "VIEWER", "vw12345"),
]


def dev_hash(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    b64 = lambda data: base64.b64encode(data).decode("ascii")  # noqa: E731
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(_scrypt(password, salt, N, R, P))}"


settings = load_settings()
engine = create_db_engine(settings)
site = site_of(load_pack(settings, engine))
mine = (User.org_id == site.org_id, User.site_id == site.site_id)
with engine.begin() as connection:
    for name, email, role, password in ACCOUNTS:
        user_id = connection.scalar(select(User.user_id).where(*mine, func.lower(User.email) == email))
        values = {"name": name, "role": role, "password_hash": dev_hash(password), "disabled_at": None}
        if user_id is None:
            connection.execute(insert(User).values(
                org_id=site.org_id, site_id=site.site_id, user_id=uuid.uuid4().hex, email=email, **values))
        else:
            # keeps the Owner's authenticator, once set up
            connection.execute(update(User).where(*mine, User.user_id == user_id).values(**values))
        print(f"  {role:<10} {email}")
engine.dispose()
