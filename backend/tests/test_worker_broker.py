"""The worker against a real broker and a real database (T1-11).

Start them with: docker compose -f infra/docker-compose.dev.yml up -d db mqtt
Without a broker on MQTT_HOST:MQTT_PORT these tests are skipped. They use their own site and
client ID, so they don't touch a simulator or a worker that is already running.
"""

import asyncio
import dataclasses
import json
import socket
import sys
import uuid

import aiomqtt
import pytest
from sqlalchemy import select
from test_worker import climate_telemetry, heartbeat, zone_telemetry

from app.config import load_settings
from app.contract import parse_farm_message
from app.db.models import Reading
from app.worker.main import run
from app.worker.store import last_seen_times, reading_rows

TIMEOUT_S = 15
ORG = "client_A"


@pytest.fixture
def site():
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def broker_settings(migrated_db, tmp_path, site):
    """Settings for a worker of its own, on a config pack with a unique site."""
    settings = load_settings()
    try:
        socket.create_connection((settings.mqtt_host, settings.mqtt_port), timeout=1).close()
    except OSError:
        pytest.skip(f"no MQTT broker on {settings.mqtt_host}:{settings.mqtt_port}")
    client_dir = tmp_path / ORG
    client_dir.mkdir()
    (client_dir / "layout.json").write_text(json.dumps({"site": site}), encoding="utf-8")
    return dataclasses.replace(settings, client_dir=client_dir, db_name=migrated_db.url.database)


def run_on_a_selector_loop(coro):
    """The event loop aiomqtt needs on Windows, as in app.worker.main."""
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(coro)


def query(engine, statement):
    with engine.connect() as connection:
        return connection.execute(statement).all()


async def start_worker(settings, caplog):
    task = asyncio.create_task(run(settings, client_id=f"CLOUD-worker-test-{uuid.uuid4().hex[:8]}"))
    async with asyncio.timeout(TIMEOUT_S):
        while not any("subscribed to" in m for m in caplog.messages):
            await asyncio.sleep(0.02)
    return task


def test_valid_invalid_and_repeated_messages_over_mqtt(migrated_db, broker_settings, site, caplog):
    caplog.set_level("INFO", logger="worker")
    zone = zone_telemetry(site=site, ts="2026-11-09T04:54:00.000Z")
    bad = zone_telemetry(site=site, ts="2026-11-09T04:54:30.000Z", readings={"ec": "1.62"})
    climate = climate_telemetry(site=site)
    status = heartbeat(site=site, ts="2026-11-09T04:55:00.000Z")

    async def scenario():
        worker = await start_worker(broker_settings, caplog)
        try:
            async with aiomqtt.Client(
                broker_settings.mqtt_host, broker_settings.mqtt_port,
                identifier=f"test-{uuid.uuid4().hex[:8]}",
            ) as client:
                for topic, payload in [
                    (f"farm/{site}/zone/Z1/telemetry", json.dumps(zone)),
                    (f"farm/{site}/zone/Z1/telemetry", json.dumps(zone)),  # a replay
                    (f"farm/{site}/zone/Z1/telemetry", json.dumps(bad)),
                    (f"farm/{site}/zone/Z1/telemetry", "not json"),
                    (f"farm/{site}/device/ZC1/status", json.dumps(status)),
                    (f"farm/{site}/climate/telemetry", json.dumps(climate)),
                ]:
                    await client.publish(topic, payload, qos=1)

            # One publisher, in order, so when the last message is stored the others are done.
            last_one = select(Reading.metric).where(Reading.device_id == "CC1")
            async with asyncio.timeout(TIMEOUT_S):
                while not await asyncio.to_thread(query, migrated_db, last_one):
                    await asyncio.sleep(0.05)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    run_on_a_selector_loop(scenario())

    zone_rows = query(
        migrated_db,
        select(Reading.metric).where(Reading.device_id == "ZC1", Reading.metric != "uptimeS"),
    )
    # One message's values: the replay and the bad one add nothing.
    assert len(zone_rows) == len(reading_rows(parse_farm_message(zone), ORG))
    assert sum("dropped a replay" in m for m in caplog.messages) == 1
    assert sum("skipping a bad message" in m for m in caplog.messages) == 2
    seen = last_seen_times(migrated_db, ORG, site)
    assert seen["ZC1"] == parse_farm_message(status).ts
