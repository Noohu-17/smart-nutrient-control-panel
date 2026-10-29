"""Heartbeats, clean stop and last will against a real broker (T4-2)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import aiomqtt
import pytest

from sim.config import load_site
from sim.devices.base import SimDevice
from sim.main import DEVICES
from sim.messages import offline_status, utc_ts
from sim.settings import SIM_ROOT

pytestmark = pytest.mark.broker
DEVICE_IDS = {d["id"] for d in DEVICES}


async def watch_statuses(settings, site, done, timeout_s=15.0):
    """Subscribe to every device status (retained ones arrive first) until done(latest)."""
    latest = {}
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                              identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(f"farm/{site}/device/+/status", qos=1)
        async with asyncio.timeout(timeout_s):
            async for message in client.messages:
                status = json.loads(message.payload)
                latest[status["device"]] = status
                if done(latest):
                    return latest
    return latest


def all_online_since(started):
    return lambda latest: set(latest) >= DEVICE_IDS and all(
        latest[d].get("online") and latest[d].get("ts", "") >= started for d in DEVICE_IDS)


def all_offline(latest):
    return set(latest) >= DEVICE_IDS and not any(latest[d]["online"] for d in DEVICE_IDS)


def test_every_device_heartbeats_then_says_offline_on_a_clean_stop(broker, run):
    site, started = load_site(broker.client_dir), utc_ts(datetime.now(UTC))

    async def scenario():
        devices = [SimDevice(d["id"], d["kind"], site, boot_id=7, heartbeat_s=0.2) for d in DEVICES]
        tasks = [asyncio.create_task(device.run(broker)) for device in devices]
        await asyncio.sleep(1.0)  # several heartbeats each
        online = await watch_statuses(broker, site, all_online_since(started))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        offline = await watch_statuses(broker, site, all_offline)
        return online, offline

    online, offline = run(scenario())
    for device_id in DEVICE_IDS:
        status = online[device_id]
        assert {"v", "type", "msgId", "ts", "site", "device", "bootId", "seq"} <= set(status)
        assert (status["type"], status["site"], status["bootId"]) == ("DEVICE_STATUS", site, 7)
        assert status["seq"] >= 2
        assert offline[device_id] == offline_status(device_id)


def test_last_will_says_offline_when_the_simulator_is_killed(broker, run, tmp_path):
    site, started = load_site(broker.client_dir), utc_ts(datetime.now(UTC))
    simulator = subprocess.Popen(
        [sys.executable, "-m", "sim.main"], cwd=SIM_ROOT,
        env={**os.environ, "SIM_STATE_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        online = run(watch_statuses(broker, site, all_online_since(started)))
    finally:
        simulator.kill()  # no clean disconnect: the broker must send each last will
        simulator.wait(timeout=10)
    offline = run(watch_statuses(broker, site, all_offline))

    assert all(online[d]["bootId"] == 1 for d in DEVICE_IDS)  # fresh state dir: first boot
    assert all(offline[d] == offline_status(d) for d in DEVICE_IDS)
