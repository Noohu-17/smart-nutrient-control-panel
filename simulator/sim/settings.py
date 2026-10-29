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
PASSWORD_PREFIX = "MQTT_PASSWORD_"


@dataclass(frozen=True)
class Settings:
    mqtt_host: str
    mqtt_port: int
    log_level: str
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
        device_passwords={
            key.removeprefix(PASSWORD_PREFIX): value
            for key, value in env.items()
            if key.startswith(PASSWORD_PREFIX) and value
        },
    )
