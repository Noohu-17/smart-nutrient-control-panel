"""Session cookies and CSRF tokens (contract §11.1, T1-35).

The cookie is "<session ID>.<user ID>.<expiry>.<stage>.<signature>": a random ID, the signed-in
user, when the session ends (Unix seconds), F, T or S for its stage (FULL, TOTP, TOTP_SETUP: see
UserSession) and an HMAC-SHA256 of all four with SESSION_SECRET. The database
keeps the ID's SHA-256 in user_session, and sign-out ends that row.

Every route checks both the signature and the database row, except Stop. Stop must work while the
database is down (§8.3 rule 9), so it checks only the signature, the expiry and that the stage is
FULL: a session signed out less than SESSION_HOURS ago can still Stop. Stop is allowed for every role (§13), so that
gives nothing a signed-in Viewer couldn't do.

The CSRF token is an HMAC of the session ID too, so it needs no database either.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

COOKIE = "sna_session"
COOKIE_PATH = "/api"
CSRF_HEADER = "X-CSRF-Token"
USER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # IDs this code and users.json make; no dots
FULL, TOTP, TOTP_SETUP = "FULL", "TOTP", "TOTP_SETUP"
_STAGE_CODES = {FULL: "F", TOTP: "T", TOTP_SETUP: "S"}
_CODE_STAGES = {code: stage for stage, code in _STAGE_CODES.items()}


@dataclass(frozen=True)
class Token:
    session_id: str
    user_id: str
    expires_at: datetime
    stage: str = FULL

    @property
    def session_hash(self) -> str:
        return session_hash(self.session_id)


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("ascii")).hexdigest()


def _sign(secret: str, *parts: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), "|".join(parts).encode("utf-8"), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).decode("ascii").rstrip("=")


def new_token(secret: str, user_id: str, now: datetime, hours: float, stage: str = FULL) -> tuple[Token, str]:
    """A new session for `user_id`: the token and the cookie value that carries it."""
    if not USER_ID.fullmatch(user_id):
        raise ValueError(f"user ID {user_id!r} can't go in a session cookie")
    token = Token(secrets.token_urlsafe(32), user_id, (now + timedelta(hours=hours)).replace(microsecond=0), stage)
    parts = (token.session_id, user_id, str(int(token.expires_at.timestamp())), _STAGE_CODES[stage])
    return token, ".".join((*parts, _sign(secret, "session", *parts)))


def read_token(secret: str, cookie: str | None, now: datetime) -> Token | None:
    """The cookie's session, when its signature is right and it hasn't expired; otherwise None."""
    try:
        session_id, user_id, expiry, stage, signature = (cookie or "").split(".")
        expires_at = datetime.fromtimestamp(int(expiry), UTC)
    except (ValueError, OverflowError, OSError):
        return None
    if stage not in _CODE_STAGES or not _same(signature, _sign(secret, "session", session_id, user_id, expiry, stage)):
        return None
    return Token(session_id, user_id, expires_at, _CODE_STAGES[stage]) if now < expires_at else None


def csrf_token(secret: str, token: Token) -> str:
    return _sign(secret, "csrf", token.session_id)


def csrf_ok(secret: str, token: Token, sent: str | None) -> bool:
    return sent is not None and _same(sent, csrf_token(secret, token))


def _same(sent: str, expected: str) -> bool:
    """Constant-time comparison. As bytes, because compare_digest refuses a str that isn't ASCII,
    and these come from the client."""
    return hmac.compare_digest(sent.encode("utf-8", "replace"), expected.encode("utf-8"))
