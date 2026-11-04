"""Telemetry on a real broker, for the whole configured farm at x288 (T4-5).

Runs what sim.main runs: one connection per layout device plus the clock task. A subscriber
checks what actually arrives: topics, QoS, retained flags, envelope, order, Track 1's models.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime

import aiomqtt
import pytest
from app.contract import parse_farm_message

from sim.clock import SimClock, TickLoop
from sim.config import (
    load_devices,
    load_site,
    load_speed_options,
    read_pack_file,
)
from sim.devices.readings import ReadingDevice
from sim.farm import load_farm
from sim.main import make_device

pytestmark = pytest.mark.broker
SPEED = 288
RUN_S = 3.0


def start_farm(settings, *, boot_id=11):
    """The devices and clock task as sim.main builds them; simTs on (playground)."""
    client_dir = settings.client_dir
    site = load_site(client_dir)
    clock = SimClock(datetime.now(UTC), SPEED, load_speed_options(client_dir))
    farm = load_farm(client_dir)
    alerts = read_pack_file(client_dir, "alerts.json")
    devices = [make_device(d, site, boot_id, farm, alerts, clock) for d in load_devices(client_dir)]
    loop = TickLoop(clock, farm, [d for d in devices if isinstance(d, ReadingDevice)])
    return site, devices, loop


async def collect(settings, site, seconds, *, started=None):
    """(topic, qos, retain, message) for all of farm/{site}/# within `seconds` of subscribing."""
    received = []
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                              identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(f"farm/{site}/#", qos=1)
        if started is not None:
            started.set()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                async for message in client.messages:
                    received.append((str(message.topic), message.qos, message.retain,
                                     json.loads(message.payload)))
    return received


async def run_farm_while_collecting(settings, seconds=RUN_S):
    site, devices, loop = start_farm(settings)
    started = asyncio.Event()
    watcher = asyncio.create_task(collect(settings, site, seconds + 1.0, started=started))
    await started.wait()
    tasks = [asyncio.create_task(d.run(settings)) for d in devices]
    tasks.append(asyncio.create_task(loop.run()))
    await asyncio.sleep(seconds)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    return site, await watcher


@pytest.fixture
def live(broker, run):
    """What a subscriber saw during one run; retained leftovers from earlier runs are dropped."""
    site, received = run(run_farm_while_collecting(broker))
    return site, [r for r in received if r[3].get("bootId") == 11 and not r[2]]


def of_type(received, msg_type):
    return [r for r in received if r[3].get("type") == msg_type]


def test_every_configured_zone_and_device_publishes_on_its_contract_topic(live, broker):
    site, received = live
    zones = [d["zone"] for d in load_devices(broker.client_dir) if "zone" in d]
    assert zones == ["Z1", "Z2"]
    for zone in zones:
        readings = [r for r in of_type(received, "ZONE_TELEMETRY") if r[3]["zone"] == zone]
        assert readings, f"no telemetry for {zone}"
        assert {r[0] for r in readings} == {f"farm/{site}/zone/{zone}/telemetry"}
    assert {r[0] for r in of_type(received, "CLIMATE_TELEMETRY")} == {
        f"farm/{site}/climate/telemetry"}
    assert {r[0] for r in of_type(received, "POWER_STATE")} == {f"farm/{site}/power/state"}


def test_every_message_on_the_wire_passes_track_1s_models(live):
    _, received = live
    assert len(received) > 20
    for _, _, _, message in received:
        parse_farm_message(message)


def test_qos_on_the_wire_is_the_contracts(live):
    _, received = live
    assert {r[1] for r in of_type(received, "ZONE_TELEMETRY")} == {0}
    assert {r[1] for r in of_type(received, "CLIMATE_TELEMETRY")} == {0}
    assert {r[1] for r in of_type(received, "POWER_STATE")} == {1}
    assert {r[1] for r in of_type(received, "DEVICE_STATUS")} == {1}  # heartbeats unchanged


def test_each_device_counts_seq_across_telemetry_and_heartbeats(live):
    _, received = live
    by_device = {}
    for _, _, _, message in received:
        by_device.setdefault(message["device"], []).append(message)
    assert set(by_device) == {"GW1", "ZC1", "ZC2", "CC1", "PM1"}
    for device, messages in by_device.items():
        seqs = sorted(m["seq"] for m in messages)
        assert seqs == list(range(1, len(seqs) + 1)), device  # none lost, none repeated
        assert len({m["msgId"] for m in messages}) == len(messages), device
    assert {m["type"] for m in by_device["ZC1"]} == {"DEVICE_STATUS", "ZONE_STATE",
                                                     "ZONE_TELEMETRY"}


def test_readings_are_60_simulated_seconds_apart_at_x288(live):
    _, received = live
    stamps = [datetime.fromisoformat(r[3]["simTs"]) for r in of_type(received, "ZONE_TELEMETRY")
              if r[3]["zone"] == "Z1"]
    gaps = {(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)}
    assert len(stamps) >= 8  # about 0.21 real seconds per reading at x288
    assert gaps == {60.0}


def test_a_new_subscriber_gets_retained_power_state_but_no_retained_telemetry(broker, run):
    async def scenario():
        site, devices, loop = start_farm(broker)
        tasks = [asyncio.create_task(d.run(broker)) for d in devices]
        tasks.append(asyncio.create_task(loop.run()))
        await asyncio.sleep(1.0)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Nothing is running now: all that arrives is what the broker kept.
        return await collect(broker, site, 1.0)

    kept = run(scenario())
    types = {r[3]["type"] for r in kept}
    assert all(r[2] for r in kept)  # delivered from the broker's store
    assert {"POWER_STATE", "ZONE_STATE", "CLIMATE_STATE"} <= types
    assert not types & {"ZONE_TELEMETRY", "CLIMATE_TELEMETRY"}
    [power] = of_type(kept, "POWER_STATE")
    assert power[1] == 1 and power[3]["device"] == "PM1"
    parse_farm_message(power[3])
    assert {r[3]["zone"] for r in of_type(kept, "ZONE_STATE")} == {"Z1", "Z2"}
    assert {r[1] for r in of_type(kept, "ZONE_STATE")} == {1}
    [climate] = of_type(kept, "CLIMATE_STATE")
    assert climate[1] == 1
    parse_farm_message(climate[3])
