"""Simulator settings, read from environment variables.

Only infrastructure settings live here (broker address, device logins, paths).
Farm-specific values come from the client config pack (contract 0 and 2.5).
main.py loads simulator/.env first; variables already set in the environment win.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

SIM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SIM_ROOT.parent
PASSWORD_PREFIX = "MQTT_PASSWORD_"


@dataclass(frozen=True)
class Settings:
    """client_dir: the client's config pack; state_dir: what survives a restart (bootId)."""

    mqtt_host: str
    mqtt_port: int
    log_level: str
    client_dir: Path
    state_dir: Path
    device_passwords: Mapping[str, str] = field(default_factory=dict, repr=False)

    def login(self, device_id: str) -> tuple[str | None, str | None]:
        """Username and password for one device (contract 3: one login per device).

        A device without a password connects anonymously, as on the dev broker.
        """
        password = self.device_passwords.get(device_id)
        return (device_id, password) if password else (None, None)


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    port_text = env.get("MQTT_PORT", "1883")
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"MQTT_PORT must be a whole number, got {port_text!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"MQTT_PORT must be 1-65535, got {port}")
    return Settings(
        mqtt_host=env.get("MQTT_HOST", "localhost"),
        mqtt_port=port,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
        client_dir=_path(env.get("SIM_CLIENT_DIR"), REPO_ROOT / "config" / "clients" / "client_A"),
        state_dir=_path(env.get("SIM_STATE_DIR"), SIM_ROOT / ".state"),
        device_passwords={
            key.removeprefix(PASSWORD_PREFIX): value
            for key, value in env.items()
            if key.startswith(PASSWORD_PREFIX) and value
        },
    )


def _path(value: str | None, default: Path) -> Path:
    """A path from the environment; relative paths are relative to simulator/."""
    if not value:
        return default
    path = Path(value)
    return path if path.is_absolute() else SIM_ROOT / path
