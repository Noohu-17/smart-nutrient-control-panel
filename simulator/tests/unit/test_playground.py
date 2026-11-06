"""The playground's farm side (T4-9): speed, reset, held values and the sensor drill.

The playground runs over the real farm model and the real zone controller, as in the simulator:
the clock task moves the farm through playground.step(), then the controller steps.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from sim.clock import SimClock
from sim.config import load_speed_options, load_zone_dose, load_zone_limits
from sim.controller.zone import ZoneController
from sim.farm import load_farm
from sim.farm_hal import FarmHal
from sim.messages import climate_telemetry, zone_telemetry
from sim.playground import Playground, PlaygroundError
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
REAL_START = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
STEP_S = 0.5
DEVICES = ["GW1", "ZC1", "ZC2", "CC1", "PM1"]


class Rig:
    def __init__(self, tmp_path, speed=60):
        self.real_s = 0.0  # the clock's real-time source: tests move it by hand
        self.clock = SimClock(
            SIM_START, speed, load_speed_options(CLIENT_A), monotonic=lambda: self.real_s
        )
        self.farm = load_farm(CLIENT_A)
        self.farm.now = SIM_START
        self.zone = self.farm.zones["Z1"]
        self.reported = []
        self.hal = FarmHal(self.farm, self.zone, tmp_path / "nvs_ZC1.json",
                           utc_now=lambda: REAL_START)
        config = {"configVersion": None, "limits": load_zone_limits(CLIENT_A, "Z1"),
                  "dose": load_zone_dose(CLIENT_A, "Z1")}
        self.controller = ZoneController(
            self.hal, "Z1", list(self.zone.day_tank_l), config, self.zone.day_tank_usable_l,
            lambda kind, body: self.reported.append((kind, json.loads(json.dumps(body)))),
            device_id="ZC1", zone_name="Zone A",
        )
        self.playground = Playground(
            self.farm, self.clock, CLIENT_A, controllers=[self.controller], device_ids=DEVICES,
            min_real_reading_s=5,
        )

    def run(self, sim_seconds, step_s=STEP_S):
        """The clock task's work: the farm through the playground, then the controller."""
        for _ in range(round(sim_seconds / step_s)):
            now = self.farm.now + timedelta(seconds=step_s)
            self.playground.step(step_s, now)
            self.controller.step(self.hal.now_ms())

    def command(self, kind, **body):
        cmd_id = str(uuid.uuid4())
        expires = REAL_START + timedelta(seconds=60)
        self.controller.handle({
            "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "cmdId": cmd_id, "kind": kind,
            "site": "siet-ph1", "device": "CLOUD",
            "expiresAt": f"{expires:%Y-%m-%dT%H:%M:%S}.000Z", "target": {"zone": "Z1"}, **body,
        })
        return cmd_id

    def dose(self, **lines):
        return self.command("DOSE", lines=[{"line": k, "ml": v} for k, v in lines.items()],
                            order=list(lines), mixWaitS=60, maxDurationS=600, tolerancePct=5)

    def states(self, cmd_id):
        return [body for kind, body in self.reported
                if kind == "CMD_STATE" and body["cmdId"] == cmd_id]

    def readings(self, zone="Z1"):
        return zone_telemetry(self.farm.zones[zone])


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def refused(action, body, field):
    with pytest.raises(PlaygroundError) as caught:
        action(body)
    error = caught.value
    assert (error.status, error.code, error.field) == (422, "VALIDATION_ERROR", field)
    assert error.body()["error"]["details"]["fields"][0]["field"] == field
    return error


# --- GET /state and POST /speed ------------------------------------------------------------


def test_the_state_names_the_demo_scenario_with_no_drills_or_holds(rig):
    assert rig.playground.state() == {
        "scenario": "Demo farm",
        "simTime": "2026-07-01T06:00:00.000Z",
        "speed": 60,
        "weather": "CLEAR",
        "faults": [],
        "holds": [],
    }


def test_a_whole_speed_from_sim_speed_is_reported_as_a_whole_number(tmp_path):
    rig = Rig(tmp_path, speed=60.0)  # SIM_SPEED=60 is read as a float
    speed = rig.playground.state()["speed"]
    assert speed == 60 and isinstance(speed, int)


def test_speed_0_pauses_the_clock_and_another_speed_runs_it_again(rig):
    rig.playground.set_speed({"x": 0})
    rig.real_s += 100
    assert rig.playground.state()["simTime"] == "2026-07-01T06:00:00.000Z"
    assert rig.playground.state()["speed"] == 0
    rig.playground.set_speed({"x": 288})
    rig.real_s += 10
    assert rig.playground.state()["simTime"] == "2026-07-01T06:48:00.000Z"  # 2880 s


@pytest.mark.parametrize("body", [{"x": 5}, {"x": "60"}, {"x": True}, {}, {"x": None}])
def test_a_speed_that_simulator_json_does_not_offer_is_refused(rig, body):
    error = refused(rig.playground.set_speed, body, "body.x")
    assert error.message == "Speed must be one of 0, 1, 60, 288"
    assert rig.clock.speed == 60


def test_a_body_that_is_not_an_object_is_refused(rig):
    refused(rig.playground.set_speed, [60], "body")


# --- held values (11.4.2) -------------------------------------------------------------------


def test_holding_the_reservoir_at_15_lowers_the_water_and_the_controller_refuses_a_dose(rig):
    rig.playground.hold({"target": "Z1", "metric": "reservoirPct", "value": 15})
    readings = rig.readings()["readings"]
    assert readings["reservoirPct"] == 15
    assert readings["ec"] == 1.10  # less water, same strength
    cmd_id = rig.dose(A=20)
    assert [(s["state"], s["reason"]) for s in rig.states(cmd_id)] == [
        ("REJECTED", "RESERVOIR_LOW")]
    assert rig.playground.state()["holds"] == [
        {"target": "Z1", "metric": "reservoirPct", "value": 15, "gradual": True}]


def test_a_held_value_stays_put_while_the_water_around_it_drifts(rig):
    rig.playground.hold({"target": "Z1", "metric": "ph", "value": 6.0, "gradual": False})
    for hour in range(1, 25):
        rig.playground.step(3600, SIM_START + timedelta(hours=hour))
    assert rig.readings("Z1")["readings"]["ph"] == 6.0
    assert rig.readings("Z2")["readings"]["ph"] > 6.04 + 0.1  # pH drifts up by itself


def test_ec_moves_to_a_held_value_over_about_two_readings(rig):
    """At x60 readings are 300 simulated seconds apart (contract 11.4), so 600 s."""
    rig.playground.hold({"target": "Z1", "metric": "ec", "value": 1.5})
    assert rig.readings()["readings"]["ec"] == 1.10
    rig.playground.step(300, SIM_START + timedelta(seconds=300))
    assert rig.readings()["readings"]["ec"] == pytest.approx(1.30, abs=0.01)
    rig.playground.step(300, SIM_START + timedelta(seconds=600))
    assert rig.readings()["readings"]["ec"] == 1.5
    rig.playground.step(3600, SIM_START + timedelta(seconds=4200))
    assert rig.readings()["readings"]["ec"] == 1.5


def test_gradual_false_jumps_at_once(rig):
    rig.playground.hold({"target": "Z1", "metric": "solTempC", "value": 28.5, "gradual": False})
    assert rig.readings()["readings"]["solTempC"] == 28.5


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"target": "Z1", "metric": "ph", "value": 15}, "body.value"),
        ({"target": "Z1", "metric": "reservoirPct", "value": -1}, "body.value"),
        ({"target": "Z1", "metric": "ph", "value": True}, "body.value"),
        ({"target": "Z1", "metric": "ph", "value": "7"}, "body.value"),
        ({"target": "Z1", "metric": "ph"}, "body.value"),
        ({"target": "Z1", "metric": "doMgL", "value": 5}, "body.metric"),
        ({"target": "Z1", "metric": "dayTankPct", "value": 50}, "body.metric"),
        ({"target": "Z1", "metric": "dayTankPct.CAL", "value": 50}, "body.metric"),
        ({"target": "Z1", "metric": "ph.A", "value": 6}, "body.metric"),
        ({"target": "Z9", "metric": "ph", "value": 6}, "body.target"),
        ({"target": "POLYHOUSE", "metric": "ph", "value": 6}, "body.metric"),
        ({"target": "Z1", "metric": "ph", "value": 6, "gradual": "yes"}, "body.gradual"),
        ("Z1 ph 6", "body"),
    ],
)
def test_a_value_out_of_range_or_that_cannot_be_held_is_refused(rig, body, field):
    refused(rig.playground.hold, body, field)
    assert rig.playground.state()["holds"] == []
    assert rig.readings()["readings"]["ph"] == 6.04


def test_the_refusal_says_what_the_range_is(rig):
    body = {"target": "Z1", "metric": "ph", "value": 15}
    error = refused(rig.playground.hold, body, "body.value")
    assert error.message == "ph must be a number from 0 to 14"


def test_a_released_flow_returns_to_normal_and_release_all_empties_the_list(rig):
    rig.playground.hold({"target": "Z1", "metric": "circFlowLpm", "value": 5})
    assert rig.readings()["readings"]["circFlowLpm"] == 5.0
    assert rig.playground.release("Z1", "circFlowLpm") is True
    assert rig.readings()["readings"]["circFlowLpm"] == 31.0
    assert rig.playground.release("Z1", "circFlowLpm") is False
    rig.playground.hold({"target": "Z1", "metric": "dayTankPct.ACID", "value": 10})
    rig.playground.hold({"target": "Z2", "metric": "ec", "value": 2.0})
    assert rig.readings()["readings"]["dayTankPct"]["ACID"] == 10
    rig.playground.release_all()
    assert rig.playground.state()["holds"] == []


def test_a_dose_releases_ec_ph_and_the_day_tanks_it_draws_from(rig):
    for metric, value in [("ec", 1.10), ("ph", 6.0), ("dayTankPct.A", 50),
                          ("dayTankPct.ACID", 50), ("reservoirPct", 78)]:
        rig.playground.hold({"target": "Z1", "metric": metric, "value": value, "gradual": False})
    rig.dose(A=20)
    rig.run(15)
    held = {hold["metric"] for hold in rig.playground.state()["holds"]}
    assert held == {"dayTankPct.ACID", "reservoirPct"}
    assert rig.readings()["readings"]["ec"] > 1.10  # the dose has its real effect


def test_a_refill_releases_the_level_and_ec_and_ph_which_it_dilutes(rig):
    for metric, value in [("reservoirPct", 20), ("ec", 1.0), ("ph", 6.0), ("solTempC", 20)]:
        rig.playground.hold({"target": "Z1", "metric": metric, "value": value, "gradual": False})
    rig.zone.outputs["Z1-REFILL"] = "OPEN"
    rig.playground.step(1, SIM_START + timedelta(seconds=1))
    assert [hold["metric"] for hold in rig.playground.state()["holds"]] == ["solTempC"]


def test_held_air_moves_every_position_and_release_puts_it_back(rig):
    before = climate_telemetry(rig.farm.climate)["readings"]
    rig.playground.hold({"target": "POLYHOUSE", "metric": "airTempC", "value": 36,
                         "gradual": False})
    rig.playground.hold({"target": "POLYHOUSE", "metric": "airRh", "value": 90})
    air = climate_telemetry(rig.farm.climate)["readings"]
    assert (air["airTempC"], air["airRh"]) == (36.0, 90)
    spread = air["airTempByPosC"]["FAN_END"] - air["airTempByPosC"]["PAD_END"]
    assert spread == pytest.approx(33.1 - 29.4)
    rig.playground.release_all()
    assert climate_telemetry(rig.farm.climate)["readings"] == before


# --- the sensor drill (11.4.3) --------------------------------------------------------------


def test_sensor_offline_on_ph_reads_null_with_fault_and_fix_restores_it(rig):
    rig.playground.fault({"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": True})
    telemetry = rig.readings()
    assert (telemetry["readings"]["ph"], telemetry["quality"]["ph"]) == (None, "FAULT")
    assert rig.hal.read_sensor("PH") == (None, "FAULT")  # the controller sees it too
    assert rig.readings("Z2")["quality"]["ph"] == "OK"
    assert rig.playground.state()["faults"] == [{"kind": "SENSOR_OFFLINE", "target": "Z1.ph"}]
    cmd_id = rig.dose(A=20)
    assert rig.states(cmd_id)[0]["reason"] == "SENSOR_STALE"

    rig.playground.fault({"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": False})
    telemetry = rig.readings()
    assert (telemetry["readings"]["ph"], telemetry["quality"]["ph"]) == (6.04, "OK")
    assert rig.playground.state()["faults"] == []


@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        ({"kind": "NOPE", "target": "Z1", "on": True}, "body.kind",
         "There is no failure drill 'NOPE'"),
        ({"kind": "SENSOR_OFFLINE", "target": "Z1", "on": True}, "body.target",
         "Sensor offline can't be aimed at 'Z1'"),
        ({"kind": "SENSOR_OFFLINE", "target": "Z1.reservoirPct", "on": True}, "body.target",
         "Sensor offline can't be aimed at 'Z1.reservoirPct'"),
        ({"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": "yes"}, "body.on",
         "on must be true (Trigger) or false (Fix)"),
        ({"kind": "LEAK", "target": "Z1", "on": True}, "body.kind",
         "Leak sensor wet isn't built in the simulator yet"),
    ],
)
def test_a_drill_is_checked_against_the_scenarios_catalogue(rig, body, field, message):
    assert refused(rig.playground.fault, body, field).message == message
    assert rig.playground.state()["faults"] == []


def test_drill_targets_follow_the_layout(rig):
    drills = rig.playground.drills
    assert rig.playground.drill_targets(drills["SENSOR_OFFLINE"]) == [
        "Z1.ec", "Z1.ph", "Z1.solTempC", "Z2.ec", "Z2.ph", "Z2.solTempC"]
    assert rig.playground.drill_targets(drills["NO_FLOW"])[:3] == ["Z1.A", "Z1.B", "Z1.ACID"]
    assert rig.playground.drill_targets(drills["POWER_CUT"]) == DEVICES


# --- POST /reset ----------------------------------------------------------------------------


def test_reset_returns_to_the_demo_starting_state(rig):
    rig.playground.set_speed({"x": 288})
    rig.playground.hold({"target": "Z1", "metric": "ec", "value": 2.0, "gradual": False})
    rig.playground.hold({"target": "Z2", "metric": "circFlowLpm", "value": 2})
    rig.playground.fault({"kind": "SENSOR_OFFLINE", "target": "Z2.ec", "on": True})
    dose = rig.dose(A=100)
    rig.run(20)
    rig.real_s += 30
    sim_time_before = rig.playground.state()["simTime"]

    rig.playground.reset()

    final = rig.states(dose)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "STOP_REQUESTED")
    assert 0 < final["lines"][0]["deliveredMl"] < 100
    state = rig.playground.state()
    assert (state["speed"], state["faults"], state["holds"]) == (60, [], [])
    assert state["simTime"] >= sim_time_before  # simulated time never goes back
    z1, z2 = rig.readings("Z1")["readings"], rig.readings("Z2")
    assert (z1["ec"], z1["ph"], z1["reservoirPct"], z1["dayTankPct"]) == (
        1.10, 6.04, 78, {"A": 64, "B": 66, "ACID": 81})
    assert (z2["readings"]["ec"], z2["readings"]["circFlowLpm"], z2["quality"]["ec"]) == (
        1.20, 31.0, "OK")
    assert rig.zone.outputs == {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                                "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}


def test_reset_leaves_nothing_stopped_and_clears_latches_and_24h_totals(rig):
    rig.dose(A=20)
    rig.run(15)
    assert rig.hal.nvs_read("doseHistory")["items"]  # the dose counted towards 24 h
    rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    rig.controller.latch("LEAK")
    assert rig.controller.stopped

    rig.playground.reset()

    assert not rig.controller.stopped and not rig.controller.latches
    assert rig.hal.nvs_read("doseHistory") == {"items": []}
    state = [body for kind, body in rig.reported if kind == "ZONE_STATE"][-1]
    assert (state["stopped"], state["latches"], state["operation"]) == (False, [], None)
    cmd_id = rig.dose(A=20)
    assert rig.states(cmd_id)[0]["state"] == "ACCEPTED"
