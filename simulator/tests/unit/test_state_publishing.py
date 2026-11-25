"""ZONE_STATE and CLIMATE_STATE: at the start, on every change, and nothing else (T4-6).

Commands arrive with the controllers, so these tests change the farm directly, as the task
says, and check what the device then publishes through the clock task and a fake MQTT client.
tests/integration/test_state_broker.py checks retained delivery on a real broker.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.contract import ClimateState, ZoneState, parse_farm_message

from sim.alarms import climate_alarms, zone_alarms
from sim.clock import SimClock, TickLoop
from sim.config import read_pack_file
from sim.devices.climate import ClimateDevice
from sim.devices.zone import ZoneDevice
from sim.farm import load_farm
from sim.messages import climate_state_topic, zone_state_topic
from sim.model.water import ReservoirState
from sim.settings import REPO_ROOT, load_settings

CLIENT_A = load_settings({}).client_dir
REAL = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
SPEEDS = (0, 1, 60, 288)
ENVELOPE_KEYS = {"v", "type", "msgId", "ts", "site", "device", "bootId", "seq"}
CMD_ID = "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44"


class FakeClient:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload, qos, retain, timeout=None):
        self.published.append({"topic": topic, "qos": qos, "retain": retain,
                               "message": json.loads(payload)})

    @property
    def messages(self):
        return self._forever()

    async def _forever(self):
        await asyncio.Event().wait()
        yield


class Rig:
    """One device, a farm, and a clock task run by hand; `real` is the fake real time."""

    def __init__(self, make, speed=60):
        self.farm = load_farm(CLIENT_A)
        self.real = 0.0
        self.clock = SimClock(SIM_START, speed, SPEEDS, monotonic=lambda: self.real)
        self.device = make(self.farm, self.clock)
        self.loop = TickLoop(self.clock, self.farm, [self.device])
        self.loop.tick()  # the start

    def tick(self, real_s=0.05):
        """One tick, by default 3 simulated s at x60: nowhere near the next reading."""
        self.real += real_s
        self.loop.tick()

    def drain(self):
        """Every message waiting in the outbox, in the order it would go out."""
        messages = [item.build() for item in self.device._outbox]
        self.device._outbox.clear()
        return messages

    def states(self):
        return [m for m in self.drain() if m["type"].endswith("_STATE")]


def make_zone(farm, clock):
    alarms = zone_alarms(read_pack_file(CLIENT_A, "alerts.json"), "Z1", ["A", "B", "ACID"])
    return ZoneDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", 412, zone=farm.zones["Z1"],
                      alarms=alarms, clock=clock, now=lambda: REAL)


def make_climate(farm, clock):
    return ClimateDevice("CC1", "CLIMATE_CONTROLLER", "siet-ph1", 77, climate=farm.climate,
                         alarms=climate_alarms(farm.climate.setpoints), clock=clock,
                         now=lambda: REAL)


@pytest.fixture
def zone_rig():
    return Rig(make_zone)


@pytest.fixture
def climate_rig():
    return Rig(make_climate)


def contract_example(heading):
    text = (REPO_ROOT / "docs" / "contract.md").read_text(encoding="utf-8")
    after_heading = text.split(f"### {heading}", 1)[1]
    return json.loads(after_heading.split("```json", 1)[1].split("```", 1)[0])


# --- 1: at the start -------------------------------------------------------------------


def test_zone_state_goes_out_at_the_start_before_the_first_reading(zone_rig):
    messages = zone_rig.drain()
    assert [m["type"] for m in messages] == ["ZONE_STATE", "ZONE_TELEMETRY"]
    state = messages[0]
    assert (state["zone"], state["mode"], state["estop"], state["stopped"]) == ("Z1", "REMOTE",
                                                                                False, False)
    assert state["outputs"] == {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                                "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}
    assert (state["operation"], state["latches"], state["configVersion"]) == (None, [], None)


def test_climate_state_goes_out_at_the_start_before_the_first_reading(climate_rig):
    messages = climate_rig.drain()
    assert [m["type"] for m in messages] == ["CLIMATE_STATE", "CLIMATE_TELEMETRY"]
    state = messages[0]
    assert (state["mode"], state["manualUntil"], state["stopped"]) == ("MANUAL", None, False)
    assert state["latches"] == []
    assert state["equipment"] == messages[1]["equipment"]  # the same as in telemetry (6.9)


def test_the_state_is_sent_at_the_start_even_while_paused():
    rig = Rig(make_zone, speed=0)
    assert [m["type"] for m in rig.drain()] == ["ZONE_STATE", "ZONE_TELEMETRY"]


# --- 2-8: every kind of change ---------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "field", "value"),
    [
        (lambda z: z.outputs.update({"Z1-DOSE-A": "ON"}), "outputs",
         {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "ON", "Z1-DOSE-B": "OFF",
          "Z1-DOSE-ACID": "OFF"}),
        (lambda z: z.outputs.update({"Z1-REFILL": "OPEN"}), "outputs",
         {"Z1-CIRC": "ON", "Z1-REFILL": "OPEN", "Z1-DOSE-A": "OFF", "Z1-DOSE-B": "OFF",
          "Z1-DOSE-ACID": "OFF"}),
        (lambda z: setattr(z, "mode", "MAINT"), "mode", "MAINT"),
        (lambda z: setattr(z, "estop", True), "estop", True),
        (lambda z: setattr(z, "stopped", True), "stopped", True),
        (lambda z: z.latches.add("LEAK"), "latches", ["LEAK"]),
        (lambda z: setattr(z, "config_version", 7), "configVersion", 7),
        (lambda z: setattr(z, "operation", {"cmdId": CMD_ID, "kind": "REFILL",
                                            "state": "EXECUTING"}), "operation",
         {"cmdId": CMD_ID, "kind": "REFILL", "state": "EXECUTING"}),
    ],
)
def test_every_kind_of_zone_change_goes_out_once(zone_rig, change, field, value):
    zone_rig.drain()
    change(zone_rig.farm.zones["Z1"])
    zone_rig.tick()
    [state] = zone_rig.states()
    assert state[field] == value
    parse_farm_message(state)
    zone_rig.tick()
    assert zone_rig.states() == []


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (lambda c: setattr(c, "fan_stage", 2),
         lambda s: (s["fanStage"], s["FAN2"], s["FAN3"]) == (2, "ON", "OFF")),
        (lambda c: c.outputs.update(PAD1="ON"), lambda s: s["PAD1"] == "ON"),
        (lambda c: c.outputs.update(SHADE1="CLOSED"), lambda s: s["SHADE1"] == "CLOSED"),
        (lambda c: c.outputs.update(HAF1="ON"), lambda s: s["HAF1"] == "ON"),
        (lambda c: c.chillers["CH2"].update(state="ON"), lambda s: s["CH2"]["state"] == "ON"),
        (lambda c: c.chillers["CH1"].update(setpointC=19.5),  # nested in the equipment
         lambda s: s["CH1"]["setpointC"] == 19.5),
    ],
)
def test_every_kind_of_climate_equipment_change_goes_out_once(climate_rig, change, check):
    climate_rig.drain()
    change(climate_rig.farm.climate)
    climate_rig.tick()
    [state] = climate_rig.states()
    assert check(state["equipment"])
    assert isinstance(parse_farm_message(state), ClimateState)
    climate_rig.tick()
    assert climate_rig.states() == []


def test_climate_mode_stop_and_latches_each_go_out(climate_rig):
    climate = climate_rig.farm.climate
    climate_rig.drain()

    climate.manual_until = SIM_START + timedelta(minutes=30)
    climate_rig.tick()
    assert climate_rig.states()[0]["manualUntil"] == "2026-07-01T06:30:00.000Z"

    climate.mode = "AUTOMATIC"
    climate_rig.tick()
    assert climate_rig.states()[0]["mode"] == "AUTOMATIC"

    climate.stopped = True
    climate_rig.tick()
    assert climate_rig.states()[0]["stopped"] is True

    climate.latches.add("CHILLER_FAULT")
    climate_rig.tick()
    assert climate_rig.states()[0]["latches"] == ["CHILLER_FAULT"]


def test_an_operation_is_reported_as_it_begins_changes_and_finishes(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()

    zone.operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "ACCEPTED", "line": None}
    zone_rig.tick()
    zone.operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "EXECUTING", "line": "A"}
    zone_rig.tick()
    zone.operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "EXECUTING", "line": "B"}
    zone_rig.tick()
    zone.operation = None
    zone_rig.tick()

    seen = [m["operation"] for m in zone_rig.states()]
    assert [o and (o["state"], o["line"]) for o in seen] == [
        ("ACCEPTED", None), ("EXECUTING", "A"), ("EXECUTING", "B"), None,
    ]


def test_active_cmd_id_in_telemetry_is_the_running_operation(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "EXECUTING", "line": "A"}
    zone_rig.device.take_reading(SIM_START)
    assert {m["type"]: m.get("activeCmdId") for m in zone_rig.drain()}["ZONE_TELEMETRY"] == CMD_ID


def test_stop_then_resume_reports_both_and_outputs_stay_with_the_controller(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.estop, zone.stopped = True, True
    zone_rig.tick()
    zone.estop = False  # released: still stopped until a person sends RESUME (contract 6.8)
    zone_rig.tick()
    zone.stopped = False
    zone_rig.tick()
    assert [(m["estop"], m["stopped"]) for m in zone_rig.states()] == [
        (True, True), (False, True), (False, False),
    ]


def test_a_latch_is_reported_when_added_and_when_cleared(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.latches.add("CABINET_POWER_CUT")
    zone_rig.tick()
    zone.latches.add("LOW_LOW")
    zone_rig.tick()
    zone.latches.discard("CABINET_POWER_CUT")
    zone_rig.tick()
    zone.latches.clear()
    zone_rig.tick()
    assert [m["latches"] for m in zone_rig.states()] == [
        ["CABINET_POWER_CUT"], ["CABINET_POWER_CUT", "LOW_LOW"], ["LOW_LOW"], [],
    ]


def test_a_change_while_the_clock_is_paused_still_goes_out():
    # A person can press the E-STOP during a pause; the farm is frozen, the panel is not.
    rig = Rig(make_zone, speed=0)
    rig.drain()
    rig.farm.zones["Z1"].estop = True
    rig.tick(real_s=1.0)
    [state] = rig.states()
    assert state["estop"] is True
    assert state["simTs"] == "2026-07-01T06:00:00.000Z"


def test_a_controller_can_report_two_transitions_within_one_tick(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone_rig.device.publish_state_if_changed(SIM_START)
    zone.outputs["Z1-DOSE-A"] = "OFF"
    zone_rig.device.publish_state_if_changed(SIM_START)
    assert [m["outputs"]["Z1-DOSE-A"] for m in zone_rig.states()] == ["ON", "OFF"]


# --- 9: nothing is sent when nothing changed ---------------------------------------------


def test_many_ticks_with_no_change_send_no_state(zone_rig, climate_rig):
    zone_rig.drain()
    climate_rig.drain()
    for _ in range(15):  # 45 simulated s: no reading falls due
        zone_rig.tick()
        climate_rig.tick()
    assert zone_rig.drain() == []
    assert climate_rig.drain() == []


def test_water_moving_is_not_a_state_change(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.reservoir = ReservoirState(900.0, 1400.0, 6.5, 24.0)  # all within the alarm limits
    zone.day_tank_l["A"] = 30.0
    zone_rig.tick()
    assert zone_rig.drain() == []


def test_setting_a_state_to_the_same_value_is_not_a_change(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.mode = "REMOTE"
    zone.outputs["Z1-CIRC"] = "ON"
    zone.latches = set()
    zone_rig.tick()
    assert zone_rig.states() == []


def test_a_change_undone_before_anyone_looks_is_no_change_to_report(zone_rig):
    # Controllers report their own transitions (test above); this is only the tick's safety net.
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone.outputs["Z1-DOSE-A"] = "OFF"
    zone_rig.tick()
    assert zone_rig.states() == []


def test_the_order_of_latches_is_not_a_change(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone.latches.update({"LEAK", "LOW_LOW"})
    zone_rig.tick()
    zone_rig.drain()
    zone.latches = {"LOW_LOW", "LEAK"}
    zone_rig.tick()
    assert zone_rig.states() == []


def test_run_time_counting_up_is_not_a_climate_state_change(climate_rig):
    climate = climate_rig.farm.climate
    climate.fan_stage = 1
    climate_rig.tick()
    climate_rig.drain()
    climate.run_time_s["FANS"] += 600.0
    climate_rig.tick()
    assert climate_rig.states() == []


def test_a_message_built_earlier_keeps_what_was_true_then(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone.latches.add("LEAK")
    zone_rig.tick()  # queued, not sent yet
    zone.outputs["Z1-DOSE-A"] = "OFF"
    zone.latches.clear()
    [state] = zone_rig.states()
    assert (state["outputs"]["Z1-DOSE-A"], state["latches"]) == ("ON", ["LEAK"])


# --- contract 6.8 and 6.9: which messages go with telemetry ------------------------------


def test_zone_state_also_goes_with_each_periodic_reading(zone_rig):
    zone_rig.drain()
    for _ in range(4):
        zone_rig.tick(real_s=1.0)  # one reading per tick at x60
    types = [m["type"] for m in zone_rig.drain()]
    assert types == ["ZONE_STATE", "ZONE_TELEMETRY"] * 4


def test_zone_state_goes_with_an_alarm_reading_too_and_only_once(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.outputs["Z1-CIRC"] = "OFF"  # both a state change and a flow alarm in one tick
    zone_rig.tick()
    messages = zone_rig.drain()
    assert [m["type"] for m in messages] == ["ZONE_STATE", "ZONE_TELEMETRY"]
    assert messages[0]["outputs"]["Z1-CIRC"] == "OFF"
    assert messages[1]["readings"]["circFlowLpm"] == 0.0


def test_climate_state_goes_only_on_change_not_with_each_reading(climate_rig):
    climate_rig.drain()
    for _ in range(3):
        climate_rig.tick(real_s=1.0)
    assert [m["type"] for m in climate_rig.drain()] == ["CLIMATE_TELEMETRY"] * 3


def test_a_climate_change_due_with_a_reading_goes_once_and_first(climate_rig):
    climate_rig.drain()
    climate_rig.farm.climate.fan_stage = 3
    climate_rig.tick(real_s=1.0)  # exactly on the next reading's due time
    messages = climate_rig.drain()
    assert [m["type"] for m in messages] == ["CLIMATE_STATE", "CLIMATE_TELEMETRY"]
    assert messages[0]["equipment"]["fanStage"] == 3


def test_the_periodic_grid_does_not_move_when_state_changes(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone_rig.drain()
    zone.mode = "LOCAL"
    zone_rig.tick(real_s=0.5)  # 30 simulated s
    assert [m["type"] for m in zone_rig.drain()] == ["ZONE_STATE"]
    zone_rig.tick(real_s=0.5)  # 60 simulated s: the reading is still due now
    telemetry = [m for m in zone_rig.drain() if m["type"] == "ZONE_TELEMETRY"]
    assert [m["simTs"] for m in telemetry] == ["2026-07-01T06:01:00.000Z"]


# --- 10-13: topic, QoS, retain and envelope ----------------------------------------------


def test_topics_are_the_contract_topics():
    assert zone_state_topic("siet-ph1", "Z1") == "farm/siet-ph1/zone/Z1/state"
    assert climate_state_topic("siet-ph1") == "farm/siet-ph1/climate/state"


async def session_with(device, act=lambda: None):
    client = FakeClient()
    task = asyncio.create_task(device.session(client))
    await asyncio.sleep(0)
    act()
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return client.published


@pytest.mark.parametrize(
    ("make", "msg_type", "topic", "device_id", "boot_id"),
    [
        (make_zone, "ZONE_STATE", "farm/siet-ph1/zone/Z1/state", "ZC1", 412),
        (make_climate, "CLIMATE_STATE", "farm/siet-ph1/climate/state", "CC1", 77),
    ],
)
def test_state_is_qos_1_retained_on_its_topic_with_the_full_envelope(make, msg_type, topic,
                                                                     device_id, boot_id):
    rig = Rig(make)
    [sent] = [p for p in asyncio.run(session_with(rig.device))
              if p["message"]["type"] == msg_type]
    assert (sent["topic"], sent["qos"], sent["retain"]) == (topic, 1, True)
    message = sent["message"]
    assert set(message) >= ENVELOPE_KEYS
    assert (message["v"], message["type"], message["device"]) == (1, msg_type, device_id)
    assert (message["site"], message["bootId"]) == ("siet-ph1", boot_id)
    assert uuid.UUID(message["msgId"]).version == 4
    assert message["ts"] == "2026-10-29T09:00:00.000Z"  # real time
    assert message["simTs"] == "2026-07-01T06:00:00.000Z"  # playground: simulated time


def test_no_simTs_on_a_site_that_is_not_a_playground():
    farm = load_farm(CLIENT_A)
    device = make_zone(farm, None)
    device.publish_state(None)
    [state] = [item.build() for item in device._outbox]
    assert "simTs" not in state


def test_zone_state_passes_track_1s_model_with_everything_filled_in(zone_rig):
    zone = zone_rig.farm.zones["Z1"]
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone.operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "EXECUTING", "line": "A"}
    zone.config_version = 7
    zone.latches.add("LOW_LOW")
    zone.stopped = True
    zone_rig.drain()
    zone_rig.tick()
    [state] = zone_rig.states()
    assert isinstance(parse_farm_message(state), ZoneState)


def test_zone_state_matches_the_contract_example_field_for_field(zone_rig):
    example = contract_example("6.8")
    zone = zone_rig.farm.zones["Z1"]
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone.operation = dict(example["operation"], cmdId=CMD_ID)
    zone.config_version = example["configVersion"]
    zone_rig.drain()
    zone_rig.tick()
    [state] = zone_rig.states()
    assert set(state) - {"simTs"} == set(example)
    for field in ("zone", "mode", "estop", "stopped", "outputs", "latches", "configVersion"):
        assert state[field] == example[field], field
    assert state["operation"] == dict(example["operation"], cmdId=CMD_ID)


def test_climate_state_passes_track_1s_model_with_everything_filled_in(climate_rig):
    climate = climate_rig.farm.climate
    climate.fan_stage = 2
    climate.manual_until = SIM_START + timedelta(minutes=30)
    climate.stopped = True
    climate.latches.add("CHILLER_FAULT")
    climate.chillers["CH1"].update(state="ON", setpointC=20.0)
    climate_rig.drain()
    climate_rig.tick()
    [state] = climate_rig.states()
    assert isinstance(parse_farm_message(state), ClimateState)


def test_climate_state_matches_the_contract_example_field_for_field(climate_rig):
    example = contract_example("6.9")
    climate = climate_rig.farm.climate
    climate.fan_stage = 2
    climate.outputs.update(PAD1="ON", HAF1="ON")
    for chiller in climate.chillers.values():
        chiller["state"] = "ON"
    climate.manual_until = datetime.fromisoformat(example["manualUntil"])
    climate_rig.drain()
    climate_rig.tick()
    [state] = climate_rig.states()
    assert set(state) - {"simTs"} == set(example)
    for field in ("mode", "manualUntil", "stopped", "equipment", "latches"):
        assert state[field] == example[field], field


# --- 14: one count across all the message types ------------------------------------------


def test_seq_runs_through_state_telemetry_and_heartbeats_in_publish_order():
    rig = Rig(make_zone)
    rig.device.heartbeat_s = 0.002
    zone = rig.farm.zones["Z1"]

    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(rig.device.session(client))
        for step in range(30):
            zone.mode = "LOCAL" if step % 2 else "REMOTE"
            rig.tick(real_s=0.25)
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return [p["message"] for p in client.published if "seq" in p["message"]]

    sent = asyncio.run(scenario())
    assert {m["type"] for m in sent} == {"DEVICE_STATUS", "ZONE_STATE", "ZONE_TELEMETRY"}
    assert [m["seq"] for m in sent] == list(range(1, len(sent) + 1))
    assert len({m["msgId"] for m in sent}) == len(sent)
    assert {m["bootId"] for m in sent} == {412}


# --- 16: reconnection ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("make", "msg_type"), [(make_zone, "ZONE_STATE"), (make_climate, "CLIMATE_STATE")]
)
def test_state_is_sent_again_when_the_link_comes_back(make, msg_type):
    # The dev broker keeps no retained messages over a restart, so a new subscriber
    # would otherwise see nothing until the next change.
    rig = Rig(make)
    first = asyncio.run(session_with(rig.device))
    assert len([p for p in first if p["message"]["type"] == msg_type]) == 1
    second = asyncio.run(session_with(rig.device))
    assert len([p for p in second if p["message"]["type"] == msg_type]) == 1


def test_a_change_during_an_outage_is_the_state_sent_on_reconnect():
    rig = Rig(make_zone)
    asyncio.run(session_with(rig.device))
    rig.farm.zones["Z1"].mode = "MAINT"  # no session: the link is down
    rig.tick()
    second = asyncio.run(session_with(rig.device))
    states = [p["message"] for p in second if p["message"]["type"] == "ZONE_STATE"]
    assert states and states[-1]["mode"] == "MAINT"


def test_reconnecting_does_not_turn_later_ticks_into_changes():
    rig = Rig(make_zone)
    asyncio.run(session_with(rig.device))
    asyncio.run(session_with(rig.device))
    rig.drain()
    rig.tick()
    assert rig.states() == []


def test_a_real_farm_keeps_state_quiet_while_physics_runs():
    # One simulated hour: the solution warms and pH drifts, but only the contract's
    # with-each-reading ZONE_STATE goes out, and nothing about it changes.
    rig = Rig(make_zone, speed=60)
    rig.drain()
    for _ in range(600):
        rig.tick(real_s=0.1)
    messages = rig.drain()
    states = [m for m in messages if m["type"] == "ZONE_STATE"]
    telemetry = [m for m in messages if m["type"] == "ZONE_TELEMETRY"]
    assert len(states) == len(telemetry) == 60
    assert telemetry[0]["readings"]["solTempC"] < telemetry[-1]["readings"]["solTempC"]
    keep = ("mode", "estop", "stopped", "outputs", "operation", "latches", "configVersion")
    assert len({json.dumps({k: s[k] for k in keep}) for s in states}) == 1
