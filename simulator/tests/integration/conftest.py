"""Shared set-up for tests against a real MQTT broker (marked `broker`).

Start one with: docker compose -f infra/docker-compose.dev.yml up -d mqtt
Without a broker on MQTT_HOST:MQTT_PORT these tests are skipped.
"""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest

from sim.settings import Settings, load_settings


@pytest.fixture
def broker() -> Settings:
    settings = load_settings()
    try:
        with socket.create_connection((settings.mqtt_host, settings.mqtt_port), timeout=1):
            pass
    except OSError:
        pytest.skip(f"no MQTT broker on {settings.mqtt_host}:{settings.mqtt_port}")
    return settings


@pytest.fixture
def run():
    """Run a coroutine on the event loop aiomqtt needs (selector loop on Windows)."""

    def run_coroutine(coro):
        loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(coro)

    return run_coroutine
