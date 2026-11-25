"""A zone device with its controller: commands in, reports out, steps from the clock task (T4-8)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.contract import parse_farm_message
from app.contract.watch import check

from sim.clock import (
    CONTROL_STEP_S,
    MAX_CATCH_UP_READINGS,
    MAX_CONTROL_STEPS,
    SimClock,
    TickLoop,
)
from sim.config import read_pack_file
from sim.controller.zone import ZoneController
from sim.farm import load_farm
from sim.main import controllers_of, make_device
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SPEEDS = (0, 1, 60, 288)
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)


class FakeRealTime:
    def __init__(self):
        self.s = 1000.0

    def __call__(self):
        return self.s


class FakeClient:
    """Records subscriptions and publishes; delivers the messages a test puts in."""

    def __init__(self):
        self.subscribed, self.published = [], []
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, topic, qos):
        self.subscribed.append((topic, qos))

    async def publish(self, topic, payload, qos, retain, timeout=None):
        self.published.append({"topic": topic, "qos": qos, "retain": retain,
                               "message": json.loads(payload)})

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        while True:
            topic, payload = await self.inbox.get()
            yield type("Message", (), {"topic": topic, "payload": payload})()


class Rig:
    """The real farm and devices as sim.main builds them, with a clock task driven by hand."""

    def __init__(self, tmp_path, speed=60):
        self.real = FakeRealTime()
        self.clock = SimClock(SIM_START, speed, SPEEDS, monotonic=self.real)
        self.farm = load_farm(CLIENT_A)
        self.farm.now = self.clock.last_tick
        alerts = read_pack_file(CLIENT_A, "alerts.json")
        entry = {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"}
        self.device = make_device(entry, "siet-ph1", 3, self.farm, alerts, self.clock,
                                  client_dir=CLIENT_A, state_dir=tmp_path)
        self.loop = TickLoop(self.clock, self.farm, [self.device],
                             controllers=controllers_of([self.device]))
        self.loop.tick()

    def tick(self, real_s=0.1):
        self.real.s += real_s
        self.loop.tick()

    def drain(self):
        messages = [(item.topic, item.qos, item.retain, item.build())
                    for item in self.device._outbox]
        self.device._outbox.clear()
        return messages


def command(kind, **body):
    issued = datetime.now(UTC)
    stamp = lambda t: f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z"  # noqa: E731
    return {
        "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(issued),
        "site": "siet-ph1", "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": kind,
        "issuedAt": stamp(issued), "expiresAt": stamp(issued + timedelta(seconds=60)),
        "issuedBy": "user:42", **body,
    }


def dose(**lines):
    return command("DOSE", target={"zone": "Z1"},
                   lines=[{"line": line, "ml": ml} for line, ml in lines.items()],
                   order=list(lines), mixWaitS=60, maxDurationS=600, tolerancePct=5)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# --- wiring ---------------------------------------------------------------------------------------


def test_a_zone_device_from_the_layout_gets_its_controller_and_nvs_in_the_state_folder(rig,
                                                                                         tmp_path):
    assert isinstance(rig.device.controller, ZoneController)
    rig.device.on_message(rig.device.cmd_topic, json.dumps(dose(A=10)))
    assert (tmp_path / "nvs_ZC1.json").exists()
    assert controllers_of([rig.device]) == [rig.device]  # stepped through the device (T4-11)


def test_without_a_state_folder_a_zone_device_has_no_controller_and_listens_to_nothing():
    farm = load_farm(CLIENT_A)
    entry = {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"}
    device = make_device(entry, "siet-ph1", 1, farm, {}, None)
    assert device.controller is None and device.subscriptions() == ()


def test_it_subscribes_to_its_zones_commands_and_every_stop_at_qos_1(rig):
    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(rig.device.session(client))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client.subscribed

    assert asyncio.run(scenario()) == [("farm/siet-ph1/zone/Z1/cmd", 1),
                                       ("farm/siet-ph1/stop", 1)]


def test_a_command_from_the_broker_reaches_the_controller_and_cmd_state_goes_out(rig):
    cmd = dose(A=10)

    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(rig.device.session(client))
        await client.inbox.put(("farm/siet-ph1/zone/Z1/cmd", json.dumps(cmd).encode()))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client.published

    states = [p for p in asyncio.run(scenario()) if p["message"]["type"] == "CMD_STATE"]
    assert [p["message"]["state"] for p in states] == ["ACCEPTED", "EXECUTING"]
    for published in states:
        assert published["topic"] == "farm/siet-ph1/zone/Z1/cmd/state"
        assert (published["qos"], published["retain"]) == (1, False)
        assert published["message"]["cmdId"] == cmd["cmdId"]
        assert check(json.dumps(published["message"]).encode())[0] == "OK"


@pytest.mark.parametrize("payload", [b"not json", b"[1, 2]", b'"DOSE"'])
def test_a_message_that_isnt_a_command_is_dropped_and_logged(rig, caplog, payload):
    rig.drain()
    with caplog.at_level(logging.WARNING, logger="sim.devices"):
        rig.device.on_message(rig.device.cmd_topic, payload)
    assert rig.drain() == []
    assert "dropped" in caplog.text


def test_a_command_that_breaks_the_controller_is_logged_and_the_device_carries_on(rig, caplog,
                                                                                  monkeypatch):
    def boom(cmd):
        raise RuntimeError("boom")

    monkeypatch.setattr(rig.device.controller, "handle", boom)
    with caplog.at_level(logging.ERROR, logger="sim.devices"):
        rig.device.on_message(rig.device.cmd_topic, json.dumps(dose(A=10)))
    assert "could not handle" in caplog.text


# --- reports onto the contract topics -------------------------------------------------------------


def test_the_controllers_zone_state_goes_out_once_retained_with_its_operation(rig):
    rig.drain()
    cmd = dose(A=100)  # 50 simulated seconds: the line still runs after the next tick
    rig.device.on_message(rig.device.cmd_topic, json.dumps(cmd))
    states = [m for topic, qos, retain, m in rig.drain() if m["type"] == "ZONE_STATE"]
    assert states[-1]["operation"] == {"cmdId": cmd["cmdId"], "kind": "DOSE",
                                       "state": "EXECUTING", "line": "A"}
    assert states[-1]["outputs"]["Z1-DOSE-A"] == "ON"
    for state in states:
        parse_farm_message(state)
    rig.tick()
    assert not [m for _, _, _, m in rig.drain() if m["type"] == "ZONE_STATE"]  # no duplicates


def test_telemetry_shows_the_running_command_and_pump(rig):
    cmd = dose(A=100)
    rig.device.on_message(rig.device.cmd_topic, json.dumps(cmd))
    rig.drain()
    rig.device.take_reading(rig.farm.now)
    [reading] = [m for _, _, _, m in rig.drain() if m["type"] == "ZONE_TELEMETRY"]
    assert reading["activeCmdId"] == cmd["cmdId"]
    assert reading["equipment"]["Z1-DOSE-A"] == "ON"
    parse_farm_message(reading)


def test_a_stop_on_the_stop_topic_ends_the_dose_and_stops_the_zone(rig):
    cmd = dose(A=300)  # 150 simulated seconds
    rig.device.on_message(rig.device.cmd_topic, json.dumps(cmd))
    rig.tick(1.0)  # 60 simulated seconds at x60: well into the line
    rig.drain()
    stop = command("STOP", target={"scope": "ALL_OPERATIONS"},
                   expiresAt=command("STOP")["expiresAt"])
    rig.device.on_message(rig.device.stop_topic, json.dumps(stop))
    out = rig.drain()
    states = {m["cmdId"]: m["state"] for _, _, _, m in out if m["type"] == "CMD_STATE"}
    assert states == {cmd["cmdId"]: "STOPPED", stop["cmdId"]: "COMPLETED"}
    zone_state = [m for _, _, _, m in out if m["type"] == "ZONE_STATE"][-1]
    assert (zone_state["stopped"], zone_state["operation"]) == (True, None)
    assert rig.farm.zones["Z1"].outputs["Z1-CIRC"] == "ON"


def test_cmd_state_carries_the_farms_simulated_time(rig):
    rig.tick(1.0)
    rig.drain()
    rig.device.on_message(rig.device.cmd_topic, json.dumps(dose(A=10)))
    [accepted] = [m for _, _, _, m in rig.drain() if m.get("state") == "ACCEPTED"]
    assert accepted["simTs"] == "2026-07-01T06:01:00.000Z"


def test_a_dose_runs_to_completed_through_the_clock_task_and_ec_rises(rig):
    cmd = dose(A=120)
    ec_before = rig.farm.zones["Z1"].water().ec_ms_cm
    rig.device.on_message(rig.device.cmd_topic, json.dumps(cmd))
    for _ in range(30):
        rig.tick(0.1)  # 3 real seconds at x60: 180 simulated
    states = [m for _, _, _, m in rig.drain()
              if m["type"] == "CMD_STATE" and m["cmdId"] == cmd["cmdId"]]
    assert [s["state"] for s in states][-2:] == ["COMPLETED", "COMPLETED"]
    assert states[-1]["after"]["ec"] > states[-1]["before"]["ec"]
    assert rig.farm.zones["Z1"].water().ec_ms_cm > ec_before


# --- the clock task steps the controllers ---------------------------------------------------------


class CountingController:
    def __init__(self, farm):
        self.farm, self.steps = farm, []

    def step(self, now_ms):
        assert now_ms == round(self.farm.now.timestamp() * 1000)  # the farm got there first
        self.steps.append(now_ms)


def loop_with_counter(speed):
    real = FakeRealTime()
    clock = SimClock(SIM_START, speed, SPEEDS, monotonic=real)
    farm = load_farm(CLIENT_A)
    farm.now = clock.last_tick
    counter = CountingController(farm)
    return TickLoop(clock, farm, [], controllers=[counter]), real, counter


def test_controllers_step_every_half_simulated_second_at_any_speed():
    loop, real, counter = loop_with_counter(288)
    loop.tick()
    counter.steps.clear()
    real.s += 0.1  # 28.8 simulated seconds
    loop.tick()
    gaps = {b - a for a, b in zip(counter.steps, counter.steps[1:], strict=False)}
    assert max(gaps) <= CONTROL_STEP_S * 1000
    assert counter.steps[-1] - counter.steps[0] == pytest.approx(28.8 * 1000 - max(gaps), abs=1)


def test_controllers_still_step_while_the_clock_is_paused():
    loop, real, counter = loop_with_counter(0)
    for _ in range(5):
        real.s += 1.0
        loop.tick()
    assert len(counter.steps) >= 5  # the panel is live while the farm is frozen


def test_after_a_long_stall_the_steps_get_longer_not_more():
    loop, real, counter = loop_with_counter(288)
    loop.tick()
    counter.steps.clear()
    real.s += 3600.0  # a laptop asleep: 288 simulated hours
    loop.tick()
    # One budget for the tick, plus a part-step at each of the readings it catches up on.
    assert len(counter.steps) <= MAX_CONTROL_STEPS + MAX_CATCH_UP_READINGS + 1


def test_without_controllers_the_farm_moves_in_one_step_as_before():
    real = FakeRealTime()
    clock = SimClock(SIM_START, 60, SPEEDS, monotonic=real)

    class Farm:
        def __init__(self):
            self.steps = []

        def step(self, dt_s, now):
            self.steps.append(dt_s)

    farm = Farm()
    loop = TickLoop(clock, farm, [])
    loop.tick()
    farm.steps.clear()
    real.s += 0.5
    loop.tick()
    assert farm.steps == [30.0]
