"""Checks behind GET /api/health: can the back end reach PostgreSQL and the MQTT broker?

Each check returns UP or DOWN. Failure details go to the log only, because the
health endpoint is public (contract §11.2).
"""

import asyncio
import logging
import time
import uuid

import paho.mqtt.client as mqtt
import psycopg

from .config import Settings

log = logging.getLogger(__name__)

UP = "UP"
DOWN = "DOWN"


async def check_database(settings: Settings) -> str:
    """Connect to PostgreSQL and run SELECT 1."""
    try:
        async with asyncio.timeout(settings.health_timeout_s):
            conn = await psycopg.AsyncConnection.connect(
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
                connect_timeout=max(1, round(settings.health_timeout_s)),  # libpq takes whole seconds
            )
            async with conn:
                await conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - a health check reports DOWN, it never crashes
        log.warning("database health check failed: %r", exc)
        return DOWN
    return UP


def _broker_accepts_connection(settings: Settings) -> bool:
    """Open an MQTT 3.1.1 session, wait for the broker to accept it, then disconnect."""
    timeout_s = settings.health_timeout_s
    # A unique client ID, so the probe never takes over the back end's own
    # CLOUD-api session (contract §3: one client ID per connection).
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"CLOUD-api-health-{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv311,
        clean_session=True,
    )
    client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.connect_timeout = timeout_s
    deadline = time.monotonic() + timeout_s
    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=10)
        while not client.is_connected() and time.monotonic() < deadline:
            client.loop(timeout=0.1)
        return client.is_connected()
    finally:
        client.disconnect()


async def check_broker(settings: Settings) -> str:
    """Connect to the MQTT broker and wait for its CONNACK."""
    try:
        accepted = await asyncio.wait_for(
            asyncio.to_thread(_broker_accepts_connection, settings),
            timeout=settings.health_timeout_s + 1,
        )
    except Exception as exc:  # noqa: BLE001 - a health check reports DOWN, it never crashes
        log.warning("broker health check failed: %r", exc)
        return DOWN
    if not accepted:
        log.warning("broker health check failed: no CONNACK within %.1f s", settings.health_timeout_s)
        return DOWN
    return UP
