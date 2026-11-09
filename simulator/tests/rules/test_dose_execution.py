"""A dose or refill run to its end, by the T3-4 controller on the real farm model (T4-8).

The controller drives the farm through FarmHal, as in the simulator: its pumps really move stock
into the reservoir and its flow meters count what they delivered. The farm and the controller
step every 0.5 simulated seconds, as the clock task does.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from sim.config import load_zone_dose, load_zone_limits
from sim.controller.zone import ZoneController
from sim.farm import load_farm
from sim.farm_hal import FarmHal
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
REAL_START = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
STEP_S = 0.5


class Rig:
    def __init__(self, tmp_path, speed=1.0):
        self.farm = load_farm(CLIENT_A)
        self.farm.now = SIM_START
        self.zone = self.farm.zones["Z1"]
        self.real = REAL_START
        self.speed = speed
        self.nvs_path = tmp_path / "nvs_ZC1.json"
        self.reported = []
        self.controller = self.make_controller()

    def make_controller(self):
        hal = FarmHal(self.farm, self.zone, self.nvs_path, utc_now=lambda: self.real)
        config = {
            "configVersion": None,
            "limits": load_zone_limits(CLIENT_A, "Z1"),
            "dose": load_zone_dose(CLIENT_A, "Z1"),
        }
        return ZoneController(
            hal, "Z1", list(self.zone.day_tank_l), config, self.zone.day_tank_usable_l,
            lambda kind, body: self.reported.append((kind, json.loads(json.dumps(body)))),
            device_id="ZC1", zone_name="Zone A",
        )

    def run(self, sim_seconds):
        """The clock task's work: farm, then controller, every 0.5 simulated seconds."""
        for _ in range(round(sim_seconds / STEP_S)):
            now = self.farm.now + timedelta(seconds=STEP_S)
            self.farm.step(STEP_S, now)
            self.real += timedelta(seconds=STEP_S / self.speed)
            self.controller.step(self.controller.hal.now_ms())

    def command(self, kind, cmd_id=None, **body):
        cmd_id = cmd_id or str(uuid.uuid4())
        expires = self.real + timedelta(seconds=60)
        self.controller.handle({
            "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "cmdId": cmd_id, "kind": kind,
            "site": "siet-ph1", "device": "CLOUD",
            "expiresAt": f"{expires:%Y-%m-%dT%H:%M:%S}.{expires.microsecond // 1000:03d}Z",
            "target": {"zone": "Z1"}, **body,
        })
        return cmd_id

    def dose(self, cmd_id=None, **lines_and_settings):
        settings = {k: lines_and_settings.pop(k) for k in list(lines_and_settings)
                    if k in ("maxDurationS", "mixWaitS", "tolerancePct", "order")}
        lines = [{"line": line, "ml": ml} for line, ml in lines_and_settings.items()]
        order = settings.pop("order", [item["line"] for item in lines])
        return self.command("DOSE", cmd_id, lines=lines, order=order, **settings)

    def states(self, cmd_id):
        return [body for kind, body in self.reported
                if kind == "CMD_STATE" and body["cmdId"] == cmd_id]

    def zone_states(self):
        return [body for kind, body in self.reported if kind == "ZONE_STATE"]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# --- a dose runs to its end -----------------------------------------------------------------------


def test_a_dose_runs_line_by_line_to_completed_then_reports_after_once_mixed(rig):
    ec_before = rig.zone.water().ec_ms_cm
    cmd = rig.dose(A=120, B=60, mixWaitS=300)
    rig.run(120)  # 60 s of A, 30 s of B at 2 mL/s, with time to spare
    states = rig.states(cmd)
    assert [s["state"] for s in states][:2] == ["ACCEPTED", "EXECUTING"]
    final = states[-1]
    assert (final["state"], final["reason"], final["after"]) == ("COMPLETED", None, None)
    assert [line["state"] for line in final["lines"]] == ["DONE", "DONE"]
    for line in final["lines"]:  # a pump stops within one 0.5 s step of its target
        assert line["requestedMl"] <= line["deliveredMl"] <= line["requestedMl"] + 1
    assert final["endedAt"] is not None
    assert rig.zone_states()[-1]["operation"]["state"] == "COMPLETED"  # in its mix wait (6.8)

    rig.run(300)  # the mix wait, on the farm's own clock
    mixed = rig.states(cmd)[-1]
    assert (mixed["state"], mixed["reason"]) == ("COMPLETED", None)
    assert mixed["after"]["ec"] > mixed["before"]["ec"]  # EC really rose in the farm model
    assert rig.zone.water().ec_ms_cm > ec_before
    assert rig.zone_states()[-1]["operation"] is None
    assert rig.zone.outputs == {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                                "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}


def test_the_stock_delivered_left_the_day_tanks(rig):
    tanks_before = dict(rig.zone.day_tank_l)
    cmd = rig.dose(A=120, ACID=30)
    rig.run(120)
    delivered = {line["line"]: line["deliveredMl"] for line in rig.states(cmd)[-1]["lines"]}
    for line in ("A", "ACID"):
        assert (tanks_before[line] - rig.zone.day_tank_l[line]) * 1000 == pytest.approx(
            delivered[line], abs=0.5)
    assert rig.zone.day_tank_l["B"] == tanks_before["B"]


def test_lines_run_one_at_a_time_in_the_commands_order(rig):
    cmd = rig.dose(A=20, ACID=20, order=["ACID", "A"])
    rig.run(5)
    assert (rig.zone.outputs["Z1-DOSE-ACID"], rig.zone.outputs["Z1-DOSE-A"]) == ("ON", "OFF")
    rig.run(10)
    assert (rig.zone.outputs["Z1-DOSE-ACID"], rig.zone.outputs["Z1-DOSE-A"]) == ("OFF", "ON")
    switch = [s for s in rig.states(cmd) if s["state"] == "EXECUTING"
              and [line["state"] for line in s["lines"]] == ["DONE", "RUNNING"]]
    assert switch, "EXECUTING when one line ends and the next starts (6.6)"


def test_progress_goes_out_at_least_every_5_real_seconds_while_a_line_runs(rig):
    cmd = rig.dose(A=120)  # 60 s at x1
    rig.run(70)
    executing = [s for s in rig.states(cmd) if s["state"] == "EXECUTING"]
    assert len(executing) >= 60 // 5
    delivered = [s["lines"][0]["deliveredMl"] for s in executing]
    assert delivered == sorted(delivered) and delivered[-1] > delivered[1]  # amounts so far


def test_progress_is_on_real_time_so_a_fast_clock_doesnt_flood(tmp_path):
    rig = Rig(tmp_path, speed=288)
    cmd = rig.dose(A=120)
    rig.run(70)
    executing = [s for s in rig.states(cmd) if s["state"] == "EXECUTING"]
    assert len(executing) == 1  # 60 simulated seconds are 0.2 real seconds at x288
    assert rig.states(cmd)[-1]["state"] == "COMPLETED"


def test_the_mix_wait_is_on_simulated_time(tmp_path):
    rig = Rig(tmp_path, speed=288)
    cmd = rig.dose(A=20, mixWaitS=300)
    rig.run(10 + 299)
    assert rig.states(cmd)[-1]["after"] is None
    rig.run(2)
    assert rig.states(cmd)[-1]["after"] is not None  # about one real second at x288


def test_another_dose_during_the_mix_wait_is_busy(rig):
    rig.dose(A=10, mixWaitS=300)
    rig.run(10)
    second = rig.dose(B=10)
    assert [(s["state"], s["reason"]) for s in rig.states(second)] == [("REJECTED", "BUSY")]
    rig.run(300)
    third = rig.dose(B=10)
    assert rig.states(third)[0]["state"] == "ACCEPTED"


def test_a_zero_mixwait_reports_after_on_the_next_step(rig):
    cmd = rig.dose(A=10, mixWaitS=0)
    rig.run(6)
    assert rig.states(cmd)[-1]["after"] is not None


# --- what ends a dose early -----------------------------------------------------------------------


def test_stop_mid_dose_ends_it_stopped_with_what_was_measured(rig):
    cmd = rig.dose(A=120, B=120)
    rig.run(20)  # about 40 mL of A
    stop = rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    final = rig.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "STOP_REQUESTED")
    a, b = final["lines"]
    assert (a["state"], b["state"]) == ("SHORT", "SKIPPED")
    assert 38 <= a["deliveredMl"] <= 41
    assert b["deliveredMl"] == 0
    assert [s["state"] for s in rig.states(stop)] == ["ACCEPTED", "COMPLETED"]
    assert rig.zone.outputs["Z1-DOSE-A"] == "OFF"
    assert rig.zone_states()[-1]["stopped"] is True
    rig.run(400)
    assert rig.states(cmd)[-1] == final  # no mix wait, no `after`, nothing restarts


def test_resume_clears_stopped_but_is_refused_while_the_estop_is_pressed(rig):
    rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    rig.zone.estop = True
    rig.run(1)
    refused = rig.command("RESUME")
    assert [(s["state"], s["reason"]) for s in rig.states(refused)] == [("REJECTED", "ESTOP")]
    rig.zone.estop = False
    rig.run(1)
    resumed = rig.command("RESUME")
    assert [s["state"] for s in rig.states(resumed)] == ["ACCEPTED", "COMPLETED"]
    assert rig.zone_states()[-1]["stopped"] is False


def test_an_estop_during_the_mix_wait_leaves_the_final_state_and_no_after(rig):
    cmd = rig.dose(A=10, mixWaitS=300)
    rig.run(10)
    final = rig.states(cmd)[-1]
    rig.zone.estop = True
    rig.run(400)
    assert rig.states(cmd)[-1] == final  # contract 6.6: after stays null, state unchanged
    assert rig.zone_states()[-1]["operation"] is None
    assert rig.zone_states()[-1]["stopped"] is True


def test_a_pump_too_slow_for_maxdurations_ends_partial_timeout(rig):
    rig.zone.dose_pump_ml_per_s = {**rig.zone.dose_pump_ml_per_s, "A": 0.1}
    cmd = rig.dose(A=120, B=20, maxDurationS=60)
    rig.run(70)
    final = rig.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("PARTIAL", "TIMEOUT")
    assert [line["state"] for line in final["lines"]] == ["SHORT", "SKIPPED"]
    assert 5 <= final["lines"][0]["deliveredMl"] <= 7
    assert rig.zone.outputs["Z1-DOSE-A"] == "OFF"


def test_a_pump_delivering_nothing_ends_failed_timeout_with_no_mix_wait(rig):
    rig.zone.dose_pump_ml_per_s = {**rig.zone.dose_pump_ml_per_s, "A": 0.0}
    cmd = rig.dose(A=120, maxDurationS=30)
    rig.run(40)
    final = rig.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("FAILED", "TIMEOUT")
    assert rig.zone_states()[-1]["operation"] is None  # nothing to mix


# --- delivered mL count only once measured --------------------------------------------------------


def test_the_24h_totals_add_what_the_meters_measured(rig):
    cmd = rig.dose(A=300, mixWaitS=300)  # over before the next dose (limits.json waits longer)
    rig.run(160)
    measured = rig.states(cmd)[-1]["lines"][0]["deliveredMl"]
    history = rig.controller.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in history] == [("A", measured)]
    rig.run(300)
    # limits.json: max24hMl A 1500. Measured so far + 1200 is over it by the overshoot.
    over = rig.dose(A=1500 - measured + 1, maxDurationS=900)
    assert rig.states(over)[0]["reason"] in ("LIMIT_24H", "LIMIT_DOSE")
    within = rig.dose(A=300)
    assert rig.states(within)[0]["state"] == "ACCEPTED"


def test_a_stopped_line_counts_what_it_delivered(rig):
    rig.dose(A=120)
    rig.run(10)
    rig.command("STOP", target={"scope": "ZONE", "zone": "Z1"})
    [(_, line, ml)] = rig.controller.hal.nvs_read("doseHistory")["items"]
    assert line == "A" and 18 <= ml <= 21


def test_the_journal_carries_what_was_measured_so_far(rig):
    rig.dose(A=120)
    rig.run(20)
    journal = rig.controller.hal.nvs_read("journal")
    assert journal["state"] == "EXECUTING"
    assert journal["lines"][0]["deliveredMl"] > 0


def test_the_journal_follows_the_controllers_clock_so_a_fast_clock_loses_little(tmp_path):
    """At x288, 20 simulated seconds are 0.07 real seconds: no progress report (real time) is
    due, but a controller lost now must still count what went in (T4-11)."""
    rig = Rig(tmp_path, speed=288)
    rig.dose(A=120)
    rig.run(20)  # 40 mL at 2 mL/s
    delivered = rig.controller.hal.nvs_read("journal")["lines"][0]["deliveredMl"]
    assert 30 <= delivered <= 40  # at most JOURNAL_MS (5 s, 10 mL) behind


# --- refill to its target -------------------------------------------------------------------------


def test_a_refill_closes_at_its_target(rig):
    cmd = rig.command("REFILL", action="START", targetPct=80, maxDurationS=600)
    assert rig.zone.outputs["Z1-REFILL"] == "OPEN"
    rig.run(120)  # 24 L at 30 L/min: about 48 s
    final = rig.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("COMPLETED", None)
    assert final["detail"] == {"fromPct": 78, "toPct": 80}
    assert rig.zone.outputs["Z1-REFILL"] == "CLOSED"
    assert rig.zone_states()[-1]["operation"] is None


def test_a_refill_that_runs_out_of_time_ends_partial_timeout(rig):
    cmd = rig.command("REFILL", action="START", targetPct=100, maxDurationS=60)
    rig.run(70)
    final = rig.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("PARTIAL", "TIMEOUT")
    assert final["detail"]["fromPct"] < final["detail"]["toPct"] < 100
    assert rig.zone.outputs["Z1-REFILL"] == "CLOSED"


# --- a new run of the simulator -------------------------------------------------------------------


def test_a_new_run_closes_an_interrupted_dose_from_nvs_and_is_not_stopped(rig, tmp_path):
    cmd = rig.dose(A=120)
    rig.run(20)  # the simulator is killed here, mid-dose
    rig.reported.clear()
    again = rig.make_controller()
    again.start()
    [closed] = [b for k, b in rig.reported if k == "CMD_STATE"]
    assert (closed["cmdId"], closed["state"], closed["reason"]) == (cmd, "PARTIAL",
                                                                    "REBOOT_MID_DOSE")
    assert closed["lines"][0]["deliveredMl"] > 0  # what the journal had measured
    assert again.stopped is False  # a new run is not a reboot (agreed on #31)
    rig.reported.clear()
    again.handle({"cmdId": cmd, "kind": "DOSE"})  # the same cmdId: remembered, never run again
    assert [b["state"] for k, b in rig.reported if k == "CMD_STATE"] == ["PARTIAL"]
