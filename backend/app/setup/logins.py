"""Farm devices' MQTT logins (contract §3), issued once when a client's setup is saved (T1-40).

Each device in the new layout gets a random password. The API answers it once and keeps only its
hash, in Mosquitto's own password-file format ($7$: PBKDF2-SHA512, as mosquitto_passwd writes), in
device_login. The broker reads its logins from its password file when it starts, so they reach it
by hand, as add-device-login.sh's do (infra/readme.md, "Device logins"). From backend/:

    python -m app.setup.logins client_C      # the password-file lines for client_C's devices

Append them to the broker's secrets/passwords, give each device its block in acl.conf for the new
site, and restart the broker. The lines hold hashes only; the passwords are nowhere but where the
person who saved the setup put them.
"""

import argparse
import base64
import hashlib
import secrets
import sys

from sqlalchemy import Connection, Engine, select

from ..config import load_settings
from ..db.engine import create_db_engine
from ..db.models import DeviceLogin

ITERATIONS = 101  # mosquitto_passwd's default (Mosquitto 2.0)
SALT_BYTES = 12  # Mosquitto's salt length


def new_password() -> str:
    """24 random bytes as hex, 48 characters: the same as add-device-login.sh makes."""
    return secrets.token_hex(24)


def mosquitto_hash(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """The password as Mosquitto stores it: $7$<iterations>$<salt, base64>$<PBKDF2-SHA512, base64>."""
    salt = salt if salt is not None else secrets.token_bytes(SALT_BYTES)
    key = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, iterations, dklen=64)
    return f"$7${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def password_file_lines(connection: Connection, org_id: str) -> list[str]:
    """"<device>:<hash>" for each of the organisation's device logins, as Mosquitto reads them."""
    rows = connection.execute(
        select(DeviceLogin.device_id, DeviceLogin.password_hash)
        .where(DeviceLogin.org_id == org_id)
        .order_by(DeviceLogin.site_id, DeviceLogin.device_id)
    ).all()
    return [f"{row.device_id}:{row.password_hash}" for row in rows]


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(description="The broker password-file lines for a client's device logins (T1-40).")
    parser.add_argument("org", help="the organisation, e.g. client_C")
    args = parser.parse_args(argv)
    engine = engine or create_db_engine(load_settings())
    with engine.connect() as connection:
        lines = password_file_lines(connection, args.org)
    if not lines:
        print(f"{args.org} has no device logins: they are issued when its setup is saved", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
