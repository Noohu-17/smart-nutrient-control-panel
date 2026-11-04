"""Retained ZONE_STATE and CLIMATE_STATE on a real broker (T4-6).

The task's "done when": a new subscriber gets the current state at once. Each test starts the
real devices, changes the farm directly (commands arrive with the controllers), then connects
a brand-new subscriber and looks at what the broker hands it.
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
from sim.devices.readings import ReadingDevice
from sim.farm import load_farm
from sim.main import make_device

pytestmark = pytest.mark.broker
BOOT_ID = 21


async def retained_on(settings, topic, *, wait_s=0.5):
    """What a brand-new subscriber to `topic` receives, with its retain flag and QoS."""
    received = []
    async with aiomqtt.Client(settings.mqtt_host, settings.mqtt_port,
                              identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
        await client.subscribe(topic, qos=1)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(wait_s):
                async for message in client.messages:
                    received.append((message.retain, message.qos, json.loads(message.payload)))
    return received


@contextlib.asynccontextmanager
async def running_farm(settings):
    """The real devices and clock task, as sim.main runs them, paused so only state moves."""
    client_dir = settings.client_dir
    clock = SimClock(datetime.now(UTC), 0, load_speed_options(client_dir))
    farm = load_farm(client_dir)
    alerts = read_pack_file(client_dir, "alerts.json")
    site = load_site(client_dir)
    devices = [make_device(d, site, BOOT_ID, farm, alerts, clock)
               for d in load_devices(client_dir)]
    loop = TickLoop(clock, farm, [d for d in devices if isinstance(d, ReadingDevice)])
    tasks = [asyncio.create_task(d.run(settings)) for d in devices]
    tasks.append(asyncio.create_task(loop.run()))
    try:
        await asyncio.sleep(3.0)  # connected, and the state at the start has gone out
        yield site, farm
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_a_new_subscriber_gets_every_controllers_state_at_once(broker, run):
    async def scenario():
        async with running_farm(broker) as (site, _):
            zones = await retained_on(broker, f"farm/{site}/zone/+/state")
            climate = await retained_on(broker, f"farm/{site}/climate/state")
        return zones, climate

    zones, climate = run(scenario())
    assert {m["zone"] for _, _, m in zones} == {"Z1", "Z2"}
    for retain, qos, message in zones:
        assert (retain, qos) == (True, 1)
        assert (message["type"], message["bootId"]) == ("ZONE_STATE", BOOT_ID)
        parse_farm_message(message)
    [(retain, qos, message)] = climate
    assert (retain, qos) == (True, 1)
    assert (message["type"], message["device"], message["bootId"]) == ("CLIMATE_STATE", "CC1",
                                                                       BOOT_ID)
    parse_farm_message(message)


def test_a_zone_change_reaches_a_new_subscriber_as_the_latest_state(broker, run):
    async def scenario():
        async with running_farm(broker) as (site, farm):
            farm.zones["Z1"].outputs["Z1-DOSE-A"] = "ON"
            farm.zones["Z1"].latches.add("LEAK")
            await asyncio.sleep(1.5)  # the paused clock task still ticks once a real second
            return await retained_on(broker, f"farm/{site}/zone/Z1/state")

    [(retain, _, message)] = run(scenario())
    assert retain is True
    assert message["outputs"]["Z1-DOSE-A"] == "ON"
    assert message["latches"] == ["LEAK"]
    parse_farm_message(message)


def test_a_climate_change_reaches_a_new_subscriber_as_the_latest_state(broker, run):
    async def scenario():
        async with running_farm(broker) as (site, farm):
            farm.climate.fan_stage = 3
            farm.climate.chillers["CH1"].update(state="ON", setpointC=20.0)
            await asyncio.sleep(1.5)
            return await retained_on(broker, f"farm/{site}/climate/state")

    [(retain, _, message)] = run(scenario())
    assert retain is True
    assert message["equipment"]["fanStage"] == 3
    assert message["equipment"]["FAN3"] == "ON"
    assert message["equipment"]["CH1"] == {"state": "ON", "setpointC": 20.0}
    parse_farm_message(message)


def test_the_state_stays_available_after_the_devices_stop(broker, run):
    async def scenario():
        async with running_farm(broker) as (site, farm):
            farm.zones["Z2"].mode = "MAINT"
            await asyncio.sleep(1.5)
        return await retained_on(broker, f"farm/{site}/zone/Z2/state")

    [(retain, _, message)] = run(scenario())
    assert retain is True
    assert message["mode"] == "MAINT"


def test_a_subscriber_already_listening_sees_each_change_in_order(broker, run):
    async def scenario():
        async with running_farm(broker) as (site, farm):
            received = []
            async with aiomqtt.Client(broker.mqtt_host, broker.mqtt_port,
                                      identifier=f"test-{uuid.uuid4().hex[:8]}") as client:
                await client.subscribe(f"farm/{site}/zone/Z1/state", qos=1)
                for mode in ("LOCAL", "MAINT", "REMOTE"):
                    farm.zones["Z1"].mode = mode
                    await asyncio.sleep(1.2)
                with contextlib.suppress(TimeoutError):
                    async with asyncio.timeout(0.5):
                        async for message in client.messages:
                            received.append(json.loads(message.payload))
            return received

    received = run(scenario())
    assert [m["mode"] for m in received][-3:] == ["LOCAL", "MAINT", "REMOTE"]
    seqs = [m["seq"] for m in received]
    assert seqs == sorted(seqs)
