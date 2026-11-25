"""Plays every test vector against the zone controller (T3-4).

A vector passes when the runner finds nothing that differs from what the file says. The second
half checks the runner itself, so a green run means something: a wrong expectation, and a
controller that breaks a rule, must both fail.
"""

from __future__ import annotations

import copy

import pytest
from vector_runner import VECTOR_FILES, failures, load, matches, play

from sim.controller import checks

REFUSAL_RULES = {  # contract 9 rule 1, plus the checks that aren't on a start command
    "CIRC_PROTECTED", "INVALID_COMMAND", "EXPIRED_ON_ARRIVAL", "ESTOP", "FAULT_LATCHED",
    "ZONE_STOPPED", "NOT_REMOTE", "BUSY", "SENSOR_STALE", "LIMIT_DOSE", "LIMIT_24H",
    "DAYTANK_LOW", "RESERVOIR_LOW",
}


@pytest.mark.parametrize("path", VECTOR_FILES, ids=lambda p: p.name)
def test_vector_passes(path):
    assert failures(play(load(path))) == []


def expected_messages(vector):
    for entry in vector["timeline"]:
        yield from entry["expect"]["messages"]


def test_every_refusal_rule_is_played_by_a_vector():
    reasons = {
        expected["match"].get("reason")
        for path in VECTOR_FILES
        for expected in expected_messages(load(path))
        if expected["type"] == "CMD_STATE" and expected["match"]["state"] == "REJECTED"
    }
    assert reasons >= REFUSAL_RULES, f"no vector refuses with {REFUSAL_RULES - reasons}"


def test_a_vector_walks_every_start_check_from_refused_to_accepted():
    # The pass side of each rule, in one run: clear the cause, and the next rule answers.
    vector = load(next(p for p in VECTOR_FILES if p.name == "dose_check_precedence.json"))
    answers = [
        expected["match"].get("reason", expected["match"]["state"])
        for expected in expected_messages(vector)
        if expected["type"] == "CMD_STATE" and expected["match"]["state"] != "COMPLETED"
    ]
    assert answers[:-1] == [
        "ESTOP", "FAULT_LATCHED", "ACCEPTED", "ZONE_STOPPED", "ACCEPTED", "NOT_REMOTE",
        "SENSOR_STALE", "LIMIT_DOSE", "LIMIT_24H", "DAYTANK_LOW", "RESERVOIR_LOW", "ACCEPTED",
    ]
    assert answers[-1] == "EXECUTING"


# --- every rule has a pass and a refuse test: take it away, and a vector must notice --------------


RULES = [
    "check_circulation", "check_fields", "check_expiry", "check_estop", "check_latches",
    "check_stopped", "check_remote", "check_idle", "check_ec_ph", "check_per_dose", "check_24h",
    "check_day_tanks", "check_reservoir",
]


def some_vector_fails() -> bool:
    for path in VECTOR_FILES:
        try:
            if failures(play(load(path))):
                return True
        except (KeyError, TypeError, ValueError):  # the controller fell over: also noticed
            return True
    return False


@pytest.mark.parametrize("rule", RULES)
def test_a_vector_notices_when_a_rule_stops_refusing(monkeypatch, rule):
    monkeypatch.setattr(checks, rule, lambda *args: None)
    assert some_vector_fails(), f"no vector needs {rule} to refuse"


@pytest.mark.parametrize("rule", RULES)
def test_a_vector_notices_when_a_rule_refuses_everything(monkeypatch, rule):
    monkeypatch.setattr(checks, rule, lambda *args: "BUSY")
    assert some_vector_fails(), f"no vector needs {rule} to pass"


def test_every_rule_in_the_list_exists():
    assert all(callable(getattr(checks, rule)) for rule in RULES)


# --- the runner can fail --------------------------------------------------------------------------


def vector_named(name):
    return load(next(p for p in VECTOR_FILES if p.name == name))


def test_the_runner_fails_a_wrong_reason():
    vector = vector_named("dose_refused_estop.json")
    entry = next(e for e in vector["timeline"] if e["action"] == "DELIVER_COMMAND")
    entry["expect"]["messages"][0]["match"]["reason"] = "BUSY"
    assert any("expected 1 CMD_STATE" in problem for problem in failures(play(vector)))


def test_the_runner_fails_a_wrong_count():
    vector = vector_named("dose_refused_estop.json")
    entry = next(e for e in vector["timeline"] if e["action"] == "DELIVER_COMMAND")
    entry["expect"]["messages"][0]["count"] = 2
    assert failures(play(vector))


def test_the_runner_fails_a_message_that_must_never_be():
    vector = vector_named("dose_refused_estop.json")
    cmd_id = vector["timeline"][1]["message"]["cmdId"]
    vector["never"]["messages"].append(
        {"type": "CMD_STATE", "match": {"cmdId": cmd_id, "state": "REJECTED"}}
    )
    assert any("must never be" in problem for problem in failures(play(vector)))


def test_the_runner_fails_an_output_that_must_never_switch():
    vector = vector_named("dose_journal_before_pump.json")
    vector["never"]["outputsOn"].append("PUMP_A")
    assert any("PUMP_A was switched on" in problem for problem in failures(play(vector)))


def test_the_runner_fails_the_wrong_final_outputs():
    vector = vector_named("dose_journal_before_pump.json")
    vector["final"]["outputs"]["PUMP_A"] = False
    assert any("final outputs" in problem for problem in failures(play(vector)))


def test_the_runner_fails_calls_in_the_wrong_order():
    vector = vector_named("dose_journal_before_pump.json")
    entry = vector["timeline"][0]
    entry["expect"]["calls"] = list(reversed(entry["expect"]["calls"]))
    assert any("in order" in problem for problem in failures(play(vector)))


def test_the_runner_fails_a_controller_that_ignores_the_estop(monkeypatch):
    monkeypatch.setattr(checks, "check_estop", lambda pressed: None)
    vector = vector_named("dose_refused_estop.json")
    assert failures(play(vector))


def test_the_runner_fails_a_controller_that_runs_the_pump_before_the_journal(monkeypatch):
    from sim.controller.zone import ZoneController

    original = ZoneController._write_journal
    written = []

    def late_journal(self, operation):
        written.append(operation)
        if len(written) > 1:  # skip the first write, the one before the pump
            original(self, operation)

    monkeypatch.setattr(ZoneController, "_write_journal", late_journal)
    vector = vector_named("dose_journal_before_pump.json")
    vector["timeline"][0]["expect"]["calls"] = ["nvs_write:journal", "set_output:PUMP_A:on",
                                                "nvs_write:journal"]
    assert failures(play(copy.deepcopy(vector)))


def test_matches_compares_a_subset_recursively():
    assert matches({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}, "d": 3})
    assert matches({"a": None}, {"a": None})
    assert not matches({"a": None}, {})
    assert not matches({"a": {"b": 1}}, {"a": {"b": 2}})
    assert not matches({"a": [1]}, {"a": [1, 2]})
