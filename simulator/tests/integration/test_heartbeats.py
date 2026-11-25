"""Heartbeats, clean stop and last will against a real broker (T4-2), and the device
list from layout.json (T4-4)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime

import aiomqtt
import pytest

from sim.config import load_devices, load_site
from sim.devices.base import SimDevice
from sim.messages import offline_status, utc_ts
from sim.settings import SIM_ROOT, load_settings

pytestmark = pytest.mark.broker
DEVICES = load_devices(load_settings().client_dir)
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


async def statuses_during(settings, site, seconds):
    """Every status received within `seconds` of subscribing; retained ones arrive first."""
    received = []
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                              identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(f"farm/{site}/device/+/status", qos=1)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                async for message in client.messages:
                    received.append(json.loads(message.payload))
    return received


def all_online_since(started, device_ids=DEVICE_IDS):
    return lambda latest: set(latest) >= device_ids and all(
        latest[d].get("online") and latest[d].get("ts", "") >= started for d in device_ids)


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


def test_removing_zc2_from_layout_json_removes_its_heartbeat(broker, run, tmp_path):
    client_dir = shutil.copytree(broker.client_dir, tmp_path / "client")
    layout = json.loads((client_dir / "layout.json").read_text(encoding="utf-8"))
    layout["devices"] = [d for d in layout["devices"] if d["id"] != "ZC2"]
    (client_dir / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    remaining = DEVICE_IDS - {"ZC2"}

    site, started = load_site(client_dir), utc_ts(datetime.now(UTC))
    simulator = subprocess.Popen(
        [sys.executable, "-m", "sim.main"], cwd=SIM_ROOT,
        env={**os.environ, "SIM_CLIENT_DIR": str(client_dir), "SIM_STATE_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        online = run(watch_statuses(broker, site, all_online_since(started, remaining)))
        later = run(statuses_during(broker, site, 3.0))  # ZC2 would have started with the rest
    finally:
        simulator.kill()
        simulator.wait(timeout=10)

    assert set(online) >= remaining
    fresh_zc2 = [s for s in later if s["device"] == "ZC2" and s.get("ts", "") >= started]
    assert fresh_zc2 == [], "ZC2 was removed from layout.json but still sent a heartbeat"
