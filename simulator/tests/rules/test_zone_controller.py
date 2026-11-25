"""What the vectors can't say easily: code rules, memory across reboots, odd messages (T3-4)."""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest
from vector_runner import ScriptedHal, load, make_controller, make_rebooted

from sim.controller.zone import RECENT_CMD_MAX, ZoneController

VECTORS = Path(__file__).resolve().parents[1] / "vectors"
CONTROLLER_DIR = Path(__file__).resolve().parents[2] / "sim" / "controller"
CMD_A = "6a1e4c2e-1b7d-4a55-9f60-0d9c5a1f7e01"
CMD_B = "6a1e4c2e-1b7d-4a55-9f60-0d9c5a1f7e02"


class Rig:
    """A controller at the start of the first vector's farm, and what it reports."""

    def __init__(self):
        self.vector = load(VECTORS / "dose_refused_estop.json")
        self.hal = ScriptedHal(self.vector["start"])
        self.reported = []
        self.controller = make_controller(self.vector, self.hal, self._report)

    def _report(self, msg_type, body):
        self.reported.append((msg_type, json.loads(json.dumps(body))))

    def reboot(self, reason="REBOOT"):
        self.hal.relays_drop()
        self.reported.clear()
        self.controller = make_rebooted(self.vector, self.hal, self._report)
        self.controller.restart(reason)

    def dose(self, cmd_id=CMD_A, **changes):
        self.controller.handle({
            "kind": "DOSE",
            "cmdId": cmd_id,
            "expiresAt": "2026-11-09T04:56:00.000Z",
            "target": {"zone": "Z1"},
            "lines": [{"line": "A", "ml": 120}],
            "order": ["A"],
            **changes,
        })

    def states(self):
        return [body["state"] for kind, body in self.reported if kind == "CMD_STATE"]

    def of_type(self, msg_type):
        return [body for kind, body in self.reported if kind == msg_type]


@pytest.fixture
def rig():
    return Rig()


# --- firmware rules (Track 3 guide section 7) -----------------------------------------------------


@pytest.mark.parametrize("path", sorted(CONTROLLER_DIR.glob("*.py")), ids=lambda p: p.name)
def test_controller_code_imports_only_the_standard_library(path):
    allowed = {"__future__", "collections", "dataclasses", "datetime", "logging", "typing"}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module.split(".")[0]]
        else:
            continue
        assert set(names) <= allowed, f"{path.name} imports {names}"


# --- journal first (contract 8.3 rule 5) ----------------------------------------------------------


def test_the_journal_is_written_before_any_output(rig):
    rig.dose()
    calls = [call for _, call in rig.hal.calls]
    assert calls.index("nvs_write:journal") < calls.index("set_output:PUMP_A:on")
    assert rig.hal.nvs["journal"]["cmdId"] == CMD_A


def test_the_journal_says_the_command_is_running_and_empties_when_it_ends(rig):
    rig.dose()
    assert rig.hal.nvs["journal"]["state"] == "EXECUTING"
    rig.hal.inputs["ESTOP"] = True
    rig.controller.step(100)
    assert rig.hal.nvs["journal"] == {}


def test_a_refused_command_writes_no_journal(rig):
    rig.hal.inputs["ESTOP"] = True
    rig.dose()
    assert "journal" not in rig.hal.nvs


# --- circulation (contract 9 rule 4) --------------------------------------------------------------


def test_circulation_cannot_be_switched_through_the_controller(rig):
    with pytest.raises(ValueError, match="circulation"):
        rig.controller._switch("CIRC_PUMP", False)
    assert rig.hal.outputs["CIRC_PUMP"] is True


@pytest.mark.parametrize("scope", ["ALL_OPERATIONS", "ALL_EQUIPMENT"])
def test_no_stop_touches_circulation(rig, scope):
    rig.dose()
    rig.controller.handle({
        "kind": "STOP",
        "cmdId": CMD_B,
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"scope": scope},
    })
    assert rig.hal.outputs["CIRC_PUMP"] is True
    assert not [call for _, call in rig.hal.calls if "CIRC" in call]


def test_a_restart_runs_circulation_with_everything_else_off(rig):
    rig.dose()
    rig.reboot()
    assert rig.hal.outputs == {"CIRC_PUMP": True, "REFILL_VALVE": False, "PUMP_A": False,
                               "PUMP_B": False, "PUMP_ACID": False}


# --- one command ID, one action (contract 8.3 rule 1) ---------------------------------------------


def test_a_repeat_while_running_reports_again_without_a_second_run(rig):
    rig.dose()
    calls_before = list(rig.hal.calls)
    rig.reported.clear()
    rig.dose()
    assert rig.states() == ["EXECUTING"]
    assert rig.hal.calls == calls_before


def test_a_repeat_after_a_refusal_stays_refused_with_the_same_reason(rig):
    rig.hal.inputs["ESTOP"] = True
    rig.dose()
    rig.hal.inputs["ESTOP"] = False
    rig.reported.clear()
    rig.dose()
    [body] = rig.of_type("CMD_STATE")
    assert (body["state"], body["reason"]) == ("REJECTED", "ESTOP")
    assert rig.hal.outputs["PUMP_A"] is False


def test_the_memory_is_bounded_and_the_oldest_goes_first(rig):
    rig.hal.inputs["ESTOP"] = True
    ids = [f"id-{n}" for n in range(RECENT_CMD_MAX + 1)]
    for cmd_id in ids:
        rig.dose(cmd_id=cmd_id)
    remembered = rig.hal.nvs["recentCmds"]["items"]
    assert len(remembered) == RECENT_CMD_MAX
    assert [item["cmdId"] for item in remembered] == ids[1:]


def test_the_memory_survives_a_reboot(rig):
    rig.hal.inputs["ESTOP"] = True
    rig.dose()
    rig.hal.inputs["ESTOP"] = False
    rig.reboot()
    rig.reported.clear()
    rig.dose()
    [body] = rig.of_type("CMD_STATE")
    assert (body["state"], body["reason"]) == ("REJECTED", "ESTOP")


@pytest.mark.parametrize(
    "cmd",
    [
        {"kind": "DOSE"},
        {"kind": "DOSE", "cmdId": None},
        {"kind": "DOSE", "cmdId": 5},
        {"kind": "DOSE", "cmdId": ""},
        "not an object",
    ],
)
def test_a_command_with_no_readable_cmd_id_is_dropped_and_logged(rig, caplog, cmd):
    with caplog.at_level(logging.WARNING, logger="sim.controller"):
        rig.controller.handle(cmd)
    assert rig.reported == []
    assert "no readable cmdId" in caplog.text


# --- no replay after a reboot (contract 8.3 rule 6, 9 rule 9) -------------------------------------


def test_an_interrupted_dose_is_reported_failed_and_not_resumed(rig):
    rig.dose()
    rig.reboot()
    [body] = rig.of_type("CMD_STATE")
    assert (body["cmdId"], body["state"], body["reason"]) == (CMD_A, "FAILED", "REBOOT_MID_DOSE")
    assert rig.hal.outputs["PUMP_A"] is False
    assert rig.hal.nvs["journal"] == {}


def test_a_reboot_with_nothing_running_reports_no_command(rig):
    rig.reboot()
    assert rig.of_type("CMD_STATE") == []
    assert rig.of_type("EVENT")[0]["kind"] == "REBOOTED"


def test_a_second_reboot_doesnt_report_the_same_dose_twice(rig):
    rig.dose()
    rig.reboot()
    rig.reboot()
    assert rig.of_type("CMD_STATE") == []


def test_a_restart_leaves_the_zone_stopped_but_a_new_farm_is_not(rig):
    assert rig.controller.stopped is False
    rig.reboot()
    assert rig.controller.stopped is True
    assert rig.of_type("ZONE_STATE")[-1]["stopped"] is True


def test_latches_survive_a_reboot_until_cleared(rig):
    rig.controller.latch("LEAK")
    rig.reboot()
    assert rig.controller.latches == {"LEAK"}
    assert rig.of_type("ZONE_STATE")[-1]["latches"] == ["LEAK"]
    rig.controller.handle({
        "kind": "CLEAR_FAULT",
        "cmdId": CMD_B,
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": "Z1"},
        "fault": "LEAK",
    })
    rig.reboot()
    assert rig.controller.latches == set()


def test_24h_totals_survive_a_reboot_and_count_only_what_was_delivered(rig):
    rig.controller.record_delivered("A", 1400)
    rig.reboot()
    rig.reported.clear()
    rig.controller.stopped = False
    rig.dose(lines=[{"line": "A", "ml": 101}])
    [body] = rig.of_type("CMD_STATE")
    assert (body["state"], body["reason"]) == ("REJECTED", "LIMIT_24H")


def test_old_doses_leave_the_history_as_new_ones_are_recorded(rig):
    rig.controller.record_delivered("A", 100)
    rig.hal.now = 24 * 60 * 60 * 1000
    rig.controller.record_delivered("B", 7)
    assert rig.hal.nvs["doseHistory"]["items"] == [[24 * 60 * 60 * 1000, "B", 7]]


# --- reports --------------------------------------------------------------------------------------


def test_zone_state_goes_out_only_when_something_changed(rig):
    for now in range(0, 1000, 100):
        rig.controller.step(now)
    assert rig.reported == []


def test_cmd_state_has_every_field_of_contract_6_6(rig):
    rig.dose(lines=[{"line": "A", "ml": 120}, {"line": "B", "ml": 0}], order=["B", "A"])
    executing = rig.of_type("CMD_STATE")[-1]
    assert executing["lines"] == [
        {"line": "B", "requestedMl": 0, "deliveredMl": 0, "state": "SKIPPED"},
        {"line": "A", "requestedMl": 120, "deliveredMl": 0, "state": "RUNNING"},
    ]
    assert executing["before"] == {"ec": 1.51, "ph": 6.21}
    assert executing["after"] is None
    assert executing["startedAt"] == "2026-11-09T04:55:00.000Z"
    assert executing["endedAt"] is None
    assert executing["reason"] is None


def test_a_refused_dose_reports_its_lines_as_skipped_and_no_before(rig):
    rig.hal.inputs["ESTOP"] = True
    rig.dose()
    [body] = rig.of_type("CMD_STATE")
    assert body["lines"] == [{"line": "A", "requestedMl": 120, "deliveredMl": 0,
                              "state": "SKIPPED"}]
    assert body["before"] is None


def test_lines_run_in_the_commands_order_not_the_list_order(rig):
    rig.dose(lines=[{"line": "A", "ml": 10}, {"line": "B", "ml": 10}], order=["B", "A"])
    assert rig.hal.outputs["PUMP_B"] is True
    assert rig.hal.outputs["PUMP_A"] is False


def test_a_stopped_dose_marks_the_running_line_short_and_the_rest_skipped(rig):
    rig.dose(lines=[{"line": "A", "ml": 10}, {"line": "B", "ml": 10}], order=["A", "B"])
    rig.hal.inputs["ESTOP"] = True
    rig.hal.now = 100
    rig.controller.step(100)
    stopped = next(b for b in rig.of_type("CMD_STATE") if b["state"] == "STOPPED")
    assert [line["state"] for line in stopped["lines"]] == ["SHORT", "SKIPPED"]
    assert stopped["endedAt"] == "2026-11-09T04:55:00.100Z"


def test_a_refill_reports_where_it_started_and_no_lines(rig):
    rig.controller.handle({
        "kind": "REFILL",
        "cmdId": CMD_A,
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": "Z1"},
        "action": "START",
        "targetPct": 90,
        "maxDurationS": 1800,
    })
    executing = rig.of_type("CMD_STATE")[-1]
    assert executing["detail"] == {"fromPct": 78}
    assert "lines" not in executing
    assert rig.hal.outputs["REFILL_VALVE"] is True
    assert rig.of_type("ZONE_STATE")[-1]["outputs"]["Z1-REFILL"] == "OPEN"


def test_clearing_a_fault_that_isnt_latched_completes(rig):
    rig.controller.handle({
        "kind": "CLEAR_FAULT",
        "cmdId": CMD_A,
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": "Z1"},
        "fault": "LEAK",
    })
    assert rig.states() == ["ACCEPTED", "COMPLETED"]


def test_dosing_lines_come_from_the_config_not_a_fixed_list():
    hal = ScriptedHal({
        "inputs": {"MODE_REMOTE": True},
        "sensors": {
            "EC": {"value": 1.5, "status": "OK"},
            "PH": {"value": 6.0, "status": "OK"},
            "RESERVOIR_PCT": {"value": 50, "status": "OK"},
            "DAYTANK_BASE_PCT": {"value": 50, "status": "OK"},
        },
        "outputs": {},
        "nvs": {},
        "wallClockUtc": "2026-11-09T04:55:00.000Z",
    })
    reported = []
    limits = {"maxPerDoseMl": {"BASE": 50}, "max24hMl": {"BASE": 100},
              "minReservoirPctToDose": 10}
    controller = ZoneController(hal, "Z7", ["BASE"], {"limits": limits}, {"BASE": 10},
                                lambda kind, body: reported.append((kind, body)))
    controller.handle({
        "kind": "DOSE",
        "cmdId": CMD_A,
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": "Z7"},
        "lines": [{"line": "BASE", "ml": 40}],
        "order": ["BASE"],
    })
    assert hal.outputs["PUMP_BASE"] is True
    state = [body for kind, body in reported if kind == "ZONE_STATE"][-1]
    assert state["outputs"] == {"Z7-CIRC": "ON", "Z7-REFILL": "CLOSED", "Z7-DOSE-BASE": "ON"}
