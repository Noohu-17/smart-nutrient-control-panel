"""Authenticator-app codes (TOTP, RFC 6238) for the Owner's two-factor sign-in (contract §11.1, T1-35).

The usual settings, which every authenticator app reads: SHA-1, 6 digits, a new code every 30 s.
A code from the step before or after also counts, for a phone clock a little off. Each code works
once: the caller keeps the last step used and passes it back.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote

STEP_S = 30
DIGITS = 6
WINDOW = 1  # steps either side
ISSUER = "Smart Nutrient Advisor"


def new_secret() -> str:
    """A random 160-bit secret, in the base32 that authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def step_at(moment: datetime) -> int:
    return int(moment.timestamp()) // STEP_S


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def matching_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The step `code` belongs to, if it is right for now (± WINDOW) and newer than `last_step`."""
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = step_at(now)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if (last_step is None or step > last_step) and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, email: str) -> str:
    """What the setup QR code holds; an app can also take the secret typed in."""
    label = quote(f"{ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP_S}"
