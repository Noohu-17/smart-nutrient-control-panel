"""Back-end settings, read from environment variables.

Only infrastructure settings live here (hosts, ports, credentials, where the config
pack is). Farm-specific values come from the client config packs (contract §0 and
§2.5), never from code. The defaults match infra/docker-compose.dev.yml, so the app
also runs on the host against the dev stack.

On a server (APP_ENV anything but "dev") nothing falls back to a development value: the
database password and the session secret must be set, and the API docs stay off unless
switched on.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV = "dev"
DEV_DB_PASSWORD = "sna-dev-only"
DEV_SIM_CONTROL_URL = "http://127.0.0.1:8100"  # the simulator's control API on the laptop (T4-9)
DEV_SESSION_SECRET = "sna-dev-only-session-secret-not-for-a-server"
MIN_SESSION_SECRET = 32  # characters: `openssl rand -hex 32` gives 64


@dataclass(frozen=True)
class Settings:
    app_env: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None  # None logs in anonymously, as on the dev broker
    mqtt_password: str | None
    health_timeout_s: float
    client_dir: Path  # the client's config pack, config/clients/<client> (contract §2.5)
    api_docs_enabled: bool
    # The simulator's own control API (T4-9), reachable only from inside the server, for the
    # /api/sim routes of a playground site (contract §11.4). None: no simulator to forward to.
    sim_control_url: str | None = None
    # Signs the session cookie and the CSRF token (app.auth.sessions). Changing it signs everyone out.
    session_secret: str = DEV_SESSION_SECRET
    session_hours: float = 12.0  # a session ends this long after sign-in, used or not


def _db_password(app_env: str, password: str | None) -> str:
    if app_env == DEV:
        return password or DEV_DB_PASSWORD
    if not password or password == DEV_DB_PASSWORD:
        raise ValueError(f"APP_ENV={app_env}: DB_PASSWORD must be set, and not to the dev password")
    return password


def _session_secret(app_env: str, secret: str | None) -> str:
    if app_env == DEV and not secret:
        return DEV_SESSION_SECRET
    if not secret or secret == DEV_SESSION_SECRET or len(secret) < MIN_SESSION_SECRET:
        raise ValueError(
            f"APP_ENV={app_env}: SESSION_SECRET must be set, at least {MIN_SESSION_SECRET} characters,"
            " and not to the dev secret"
        )
    return secret


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    env = environ.get
    app_env = env("APP_ENV", DEV)
    return Settings(
        app_env=app_env,
        # 127.0.0.1, not localhost: the dev stack binds IPv4 only, and on Windows a
        # connection to localhost tries ::1 first and hangs until it times out.
        db_host=env("DB_HOST", "127.0.0.1"),
        db_port=int(env("DB_PORT", "5432")),
        db_name=env("DB_NAME", "sna"),
        db_user=env("DB_USER", "sna"),
        db_password=_db_password(app_env, env("DB_PASSWORD")),
        mqtt_host=env("MQTT_HOST", "localhost"),
        mqtt_port=int(env("MQTT_PORT", "1883")),
        mqtt_username=env("MQTT_USERNAME") or None,
        mqtt_password=env("MQTT_PASSWORD") or None,
        health_timeout_s=float(env("HEALTH_TIMEOUT_S", "3")),
        client_dir=Path(env("CLIENT_DIR", str(REPO_ROOT / "config" / "clients" / "client_A"))),
        api_docs_enabled=env("API_DOCS_ENABLED", "true" if app_env == DEV else "false").lower() == "true",
        sim_control_url=env("SIM_CONTROL_URL") or (DEV_SIM_CONTROL_URL if app_env == DEV else None),
        session_secret=_session_secret(app_env, env("SESSION_SECRET")),
        session_hours=float(env("SESSION_HOURS", "12")),
    )
