"""The reading rate cap and POWER_STATE on real time, on a real broker (T4-7).

These use shorter times than the shipped ones, so they take seconds rather than minutes:
a cap of 0.5 real seconds, and POWER_STATE repeating every real second.
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
from sim.config import load_devices, load_site, load_speed_options, read_pack_file
from sim.devices.power import PowerDevice
from sim.devices.readings import ReadingDevice
from sim.farm import load_farm
from sim.main import make_device

pytestmark = pytest.mark.broker
BOOT_ID = 47
RUN_S = 4.0


async def collect_while_running(settings, speed, *, cap_s, repeat_s=None):
    """What this run sent, as (qos, payload); the broker's retained copies don't count."""
    client_dir = settings.client_dir
    site = load_site(client_dir)
    received = []
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                              identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(f"farm/{site}/#", qos=1)
        # As in sim.main, the clock starts right before the tasks do.
        clock = SimClock(datetime.now(UTC), speed, load_speed_options(client_dir))
        farm = load_farm(client_dir)
        alerts = read_pack_file(client_dir, "alerts.json")
        devices = [make_device(d, site, BOOT_ID, farm, alerts, clock)
                   for d in load_devices(client_dir)]
        for device in devices:
            if repeat_s and isinstance(device, PowerDevice):
                device.repeat_s = repeat_s
                device._next_repeat = device._monotonic() + repeat_s
        loop = TickLoop(clock, farm, [d for d in devices if isinstance(d, ReadingDevice)],
                        min_real_reading_s=cap_s)
        tasks = [asyncio.create_task(d.run(settings)) for d in devices]
        tasks.append(asyncio.create_task(loop.run()))
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(RUN_S):
                async for message in client.messages:
                    payload = json.loads(message.payload)
                    if payload.get("bootId") == BOOT_ID and not message.retain:
                        received.append((message.qos, payload))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return received


def of_type(received, msg_type, device=None):
    return [p for _, p in received if p["type"] == msg_type
            and (device is None or p["device"] == device)]


def gaps_s(messages, field):
    stamps = [datetime.fromisoformat(m[field]) for m in messages]
    return [(b - a).total_seconds() for a, b in zip(stamps, stamps[1:], strict=False)]


def test_readings_follow_the_cap_and_the_power_monitor_stays_out_of_it(broker, run):
    received = run(collect_while_running(broker, 288, cap_s=0.5))
    readings = of_type(received, "ZONE_TELEMETRY", "ZC1")
    assert 3 <= len(readings) <= 10  # about one per 0.5 real s; 19 without the cap
    assert set(gaps_s(readings, "simTs")) == {144.0}  # 288 x 0.5 simulated seconds
    assert len(of_type(received, "CLIMATE_TELEMETRY")) <= 10
    assert len(of_type(received, "POWER_STATE")) == 1  # only the first: no 60 real s have passed
    for _, message in received:
        parse_farm_message(message)


def test_power_state_repeats_while_paused_and_nothing_else_moves(broker, run):
    received = run(collect_while_running(broker, 0, cap_s=5.0, repeat_s=1.0))
    powers = of_type(received, "POWER_STATE")
    assert len(powers) >= 2
    assert {qos for qos, p in received if p["type"] == "POWER_STATE"} == {1}
    assert len({p["simTs"] for p in powers}) == 1  # the farm is frozen
    assert len({p["msgId"] for p in powers}) == len(powers)  # new messages, not repeats of one
    assert all(gap > 0 for gap in gaps_s(powers, "ts"))  # real time goes on
    assert len(of_type(received, "ZONE_TELEMETRY", "ZC1")) == 1  # the one at the start
    assert of_type(received, "DEVICE_STATUS", "PM1")  # heartbeats carry on
