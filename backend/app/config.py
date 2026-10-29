"""Back-end settings, read from environment variables.

Only infrastructure settings live here (hosts, ports, credentials, where the config
pack is). Farm-specific values come from the client config packs (contract §0 and
§2.5), never from code. The defaults match infra/docker-compose.dev.yml, so the app
also runs on the host against the dev stack.
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    mqtt_host: str
    mqtt_port: int
    health_timeout_s: float
    client_dir: Path  # the client's config pack, config/clients/<client> (contract §2.5)


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        # 127.0.0.1, not localhost: the dev stack binds IPv4 only, and on Windows a
        # connection to localhost tries ::1 first and hangs until it times out.
        db_host=env("DB_HOST", "127.0.0.1"),
        db_port=int(env("DB_PORT", "5432")),
        db_name=env("DB_NAME", "sna"),
        db_user=env("DB_USER", "sna"),
        db_password=env("DB_PASSWORD", "sna-dev-only"),
        mqtt_host=env("MQTT_HOST", "localhost"),
        mqtt_port=int(env("MQTT_PORT", "1883")),
        health_timeout_s=float(env("HEALTH_TIMEOUT_S", "3")),
        client_dir=Path(env("CLIENT_DIR", str(REPO_ROOT / "config" / "clients" / "client_A"))),
    )
