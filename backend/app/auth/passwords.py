"""Password hashes (T1-35): scrypt from Python's own hashlib, so no extra package.

N=2^14, r=8, p=5 is one of OWASP's recommended scrypt settings (16 MiB per hash). A stored hash
names its settings, "scrypt$16384$8$5$<salt>$<hash>", so they can be raised later and older
hashes still check.
"""

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 5
SALT_BYTES = 16
KEY_BYTES = 32
MIN_LENGTH = 12  # a password typed by a person; generated ones are much longer
_MAXMEM = 64 * 1024 * 1024  # hashlib's default limit is too low for these settings


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=KEY_BYTES, maxmem=_MAXMEM)


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"a password needs at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(SALT_BYTES)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(_scrypt(password, salt, N, R, P))}"


def check_password(password: str, stored: str | None) -> bool:
    """True when `password` matches `stored`. No hash, or one this code can't read, never matches,
    but takes as long as a real check, so the answer time doesn't tell whether the email exists."""
    try:
        name, n, r, p, salt, key = (stored or "").split("$")
        if name != "scrypt":
            raise ValueError(name)
        expected = base64.b64decode(key, validate=True)
        actual = _scrypt(password, base64.b64decode(salt, validate=True), int(n), int(r), int(p))
    except (ValueError, TypeError):
        _scrypt(password, b"\0" * SALT_BYTES, N, R, P)
        return False
    return hmac.compare_digest(actual, expected)


def new_password() -> str:
    """A random password for the first Owner: 24 bytes, 32 characters to put in the password manager."""
    return secrets.token_urlsafe(24)
