"""Every rule as a plain function: one test that passes, one that refuses (T3-4).

No HAL, clock or controller here. Each check takes only what it needs, so its boundary can be
tested directly; refusal() is then tested for the contract's order.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from sim.config import load_zone_limits
from sim.controller import checks
from sim.controller.checks import Facts, refusal
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
ZONE, LINES = "Z1", ("A", "B", "ACID")
ARRIVAL_MS = checks.parse_utc_ms("2026-11-09T04:55:30.000Z")
OK = (1.5, "OK")
LIMITS = {
    "maxPerDoseMl": {"A": 300, "B": 300, "ACID": 100},
    "max24hMl": {"A": 1500, "B": 1500, "ACID": 400},
    "minReservoirPctToDose": 30,
}


def facts(**changes) -> Facts:
    return replace(
        Facts(
            now_utc_ms=ARRIVAL_MS,
            estop=False,
            latches=(),
            stopped=False,
            mode="REMOTE",
            busy=False,
            ec=OK,
            ph=OK,
            reservoir_pct=(78, "OK"),
            day_tanks_ml={"A": 32000.0, "B": 33000.0, "ACID": 40500.0},
            dosed_24h_ml={},
            device_limits=LIMITS,
        ),
        **changes,
    )


def dose(**changes) -> dict:
    return {
        "kind": "DOSE",
        "cmdId": "c",
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": ZONE},
        "lines": [{"line": "A", "ml": 120}, {"line": "B", "ml": 120}],
        "order": ["A", "B"],
        **changes,
    }


def refill(**changes) -> dict:
    return {
        "kind": "REFILL",
        "cmdId": "c",
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": ZONE},
        "action": "START",
        "targetPct": 90,
        "maxDurationS": 1800,
        **changes,
    }


def other(kind, target=None, **changes) -> dict:
    return {
        "kind": kind,
        "cmdId": "c",
        "expiresAt": "2026-11-09T04:56:00.000Z",
        "target": {"zone": ZONE} if target is None else target,
        **changes,
    }


# --- each rule: passes, refuses -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check", "passing", "refused", "code"),
    [
        (checks.check_estop, (False,), (True,), "ESTOP"),
        (checks.check_latches, ((),), (("LEAK",),), "FAULT_LATCHED"),
        (checks.check_stopped, (False,), (True,), "ZONE_STOPPED"),
        (checks.check_remote, ("REMOTE",), ("LOCAL",), "NOT_REMOTE"),
        (checks.check_remote, ("REMOTE",), ("MAINT",), "NOT_REMOTE"),
        (checks.check_idle, (False,), (True,), "BUSY"),
    ],
)
def test_a_rule_on_one_fact(check, passing, refused, code):
    assert check(*passing) is None
    assert check(*refused) == code


def test_expiry_at_or_after_expires_at_is_too_late():
    assert checks.check_expiry(1000, 999) is None
    assert checks.check_expiry(1000, 1000) == "EXPIRED_ON_ARRIVAL"  # "at or after": 8.3 rule 2
    assert checks.check_expiry(1000, 1001) == "EXPIRED_ON_ARRIVAL"


@pytest.mark.parametrize(
    ("ec", "ph", "code"),
    [
        (OK, OK, None),
        ((1.5, "STALE"), OK, "SENSOR_STALE"),
        ((None, "FAULT"), OK, "SENSOR_STALE"),
        (OK, (6.0, "STALE"), "SENSOR_STALE"),
        (OK, (None, "FAULT"), "SENSOR_STALE"),
        (OK, (6.0, "UNCALIBRATED"), "SENSOR_STALE"),  # contract 6.1: not OK, never used for dosing
        (OK, (15.0, "OUT_OF_RANGE"), "SENSOR_STALE"),
        ((None, "OK"), OK, "SENSOR_STALE"),  # OK with no value isn't a reading
    ],
)
def test_ec_and_ph_must_be_ok_to_dose(ec, ph, code):
    assert checks.check_ec_ph(ec, ph) == code


@pytest.mark.parametrize(
    ("requests", "code"),
    [
        ({"A": 300}, None),  # at the limit is allowed
        ({"A": 301}, "LIMIT_DOSE"),
        ({"A": 100, "ACID": 101}, "LIMIT_DOSE"),  # one line is enough
        ({"A": 0, "BASE": 0}, None),  # nothing asked of a line with no limit
        ({"BASE": 1}, "LIMIT_DOSE"),  # no limit known: can't be shown safe
    ],
)
def test_per_dose_limit(requests, code):
    assert checks.check_per_dose(requests, LIMITS) == code


@pytest.mark.parametrize(
    ("requests", "dosed", "code"),
    [
        ({"A": 100}, {"A": 1400}, None),  # exactly up to the limit
        ({"A": 101}, {"A": 1400}, "LIMIT_24H"),
        ({"A": 1501}, {}, "LIMIT_24H"),
        ({"B": 100}, {"A": 1500}, None),  # each line counts for itself
        ({"BASE": 1}, {}, "LIMIT_24H"),
    ],
)
def test_24h_limit(requests, dosed, code):
    assert checks.check_24h(requests, LIMITS, dosed) == code


@pytest.mark.parametrize(
    ("requests", "tanks", "code"),
    [
        ({"A": 200}, {"A": 200.0}, None),  # exactly what it holds
        ({"A": 201}, {"A": 200.0}, "DAYTANK_LOW"),
        ({"A": 1}, {"A": None}, "DAYTANK_LOW"),  # a level that can't be trusted
        ({"A": 1}, {}, "DAYTANK_LOW"),
        ({"A": 0}, {"A": None}, None),  # asks nothing of it
    ],
)
def test_day_tank_must_hold_the_request(requests, tanks, code):
    assert checks.check_day_tanks(requests, tanks) == code


@pytest.mark.parametrize(
    ("reading", "limits", "code"),
    [
        ((30, "OK"), LIMITS, None),  # at the minimum is allowed
        ((29.9, "OK"), LIMITS, "RESERVOIR_LOW"),
        ((None, "FAULT"), LIMITS, "RESERVOIR_LOW"),
        ((78, "STALE"), LIMITS, "RESERVOIR_LOW"),
        ((78, "OK"), {}, "RESERVOIR_LOW"),  # no minimum known
    ],
)
def test_reservoir_must_be_above_the_minimum(reading, limits, code):
    assert checks.check_reservoir(reading, limits) == code


@pytest.mark.parametrize(
    ("cmd", "code"),
    [
        (dose(), None),
        (refill(), None),
        (other("STOP", {"scope": "ALL_EQUIPMENT"}), None),
        (refill(target={"zone": ZONE, "equipment": "Z1-CIRC"}), "CIRC_PROTECTED"),
        (other("STOP", {"scope": "ALL_EQUIPMENT", "equipment": "Z1-CIRC"}), "CIRC_PROTECTED"),
        (other("ANYTHING", {"equipment": "Z1-CIRC"}), "CIRC_PROTECTED"),
        (refill(target={"zone": ZONE, "equipment": "Z2-CIRC"}), None),  # another zone's
    ],
)
def test_no_command_may_touch_circulation(cmd, code):
    assert checks.check_circulation(cmd, ZONE) == code


# --- well-formed commands -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        dose(),
        dose(limits={"maxPerDoseMl": {"A": 100}}),
        dose(lines=[{"line": "A", "ml": 0}, {"line": "B", "ml": 1}]),  # one line at 0 is fine
        dose(limits=None),  # the snapshot is optional: the device's own limits still apply
        refill(),
        refill(targetPct=100, maxDurationS=60),
        refill(maxDurationS=3600),
        other("RESUME"),
        other("CLEAR_FAULT", fault="LEAK"),
        other("STOP", {"scope": "ALL_OPERATIONS"}),
        other("STOP", {"scope": "ALL_EQUIPMENT"}),
        other("STOP", {"scope": "ZONE", "zone": "Z1"}),
    ],
)
def test_a_well_formed_command_passes(cmd):
    assert checks.check_fields(cmd, ZONE, LINES) is None


@pytest.mark.parametrize(
    "cmd",
    [
        dose(kind="CLIMATE_MODE"),  # not a zone controller's command
        dose(kind=None),
        dose(expiresAt=None),
        dose(expiresAt="soon"),
        dose(expiresAt="2026-11-09T04:56:00.000"),  # no Z: not contract time
        dose(target={"zone": "Z2"}),
        dose(target=None),
        dose(lines=None),
        dose(lines=[]),
        dose(lines="A"),
        dose(lines=["A"]),
        dose(lines=[{"line": "A", "ml": -5}, {"line": "B", "ml": 120}]),
        dose(lines=[{"line": "A", "ml": 12.5}, {"line": "B", "ml": 120}]),  # whole mL only
        dose(lines=[{"line": "A", "ml": "120"}, {"line": "B", "ml": 120}]),
        dose(lines=[{"line": "A", "ml": True}, {"line": "B", "ml": 120}]),
        dose(lines=[{"line": "BASE", "ml": 10}], order=["BASE"]),  # not in this zone's doseLines
        dose(lines=[{"line": "A", "ml": 1}, {"line": "A", "ml": 1}], order=["A", "A"]),
        dose(lines=[{"line": "A", "ml": 0}], order=["A"]),  # a dose of nothing
        dose(order=None),
        dose(order=["A"]),  # not every line
        dose(order=["A", "ACID"]),
        dose(limits="tight"),
        dose(limits={"maxPerDoseMl": {"A": "100"}}),
        dose(limits={"max24hMl": 100}),
        refill(target={"zone": "Z2"}),
        refill(action="PAUSE"),  # comes with refill execution
        refill(targetPct=0),
        refill(targetPct=101),
        refill(targetPct="90"),
        refill(maxDurationS=59),
        refill(maxDurationS=3601),
        other("RESUME", {"zone": "Z2"}),
        other("RESUME", {"scope": "CLIMATE"}),  # CC1's
        other("CLEAR_FAULT"),
        other("CLEAR_FAULT", fault=""),
        other("STOP", {"scope": "EVERYTHING"}),
        other("STOP", {"scope": "ZONE"}),  # ZONE needs its zone
        other("STOP", "ALL_OPERATIONS"),
    ],
)
def test_a_malformed_command_is_invalid(cmd):
    assert checks.check_fields(cmd, ZONE, LINES) == "INVALID_COMMAND"


# --- limits: the stricter of the command and limits.json ------------------------------------------


def test_the_stricter_limit_wins_line_by_line_whichever_side_it_is_on():
    device = {"maxPerDoseMl": {"A": 100, "B": 300}, "max24hMl": {"A": 1500, "B": 400}}
    command = {"maxPerDoseMl": {"A": 300, "B": 200}, "max24hMl": {"A": 900, "B": 1500}}
    merged = checks.stricter_limits(device, command)
    assert merged["maxPerDoseMl"] == {"A": 100, "B": 200}
    assert merged["max24hMl"] == {"A": 900, "B": 400}


def test_with_no_snapshot_the_devices_limits_apply():
    assert checks.stricter_limits(LIMITS, None) == LIMITS


def test_a_limit_on_one_side_only_still_applies():
    merged = checks.stricter_limits({"maxPerDoseMl": {"A": 100}},
                                    {"maxPerDoseMl": {"B": 200}})
    assert merged["maxPerDoseMl"] == {"A": 100, "B": 200}


def test_the_higher_minimum_reservoir_is_the_stricter():
    merged = checks.stricter_limits({"minReservoirPctToDose": 30}, {"minReservoirPctToDose": 40})
    assert merged["minReservoirPctToDose"] == 40
    merged = checks.stricter_limits({"minReservoirPctToDose": 50}, {"minReservoirPctToDose": 40})
    assert merged["minReservoirPctToDose"] == 50


def test_client_a_limits_come_from_limits_json():
    limits = load_zone_limits(CLIENT_A, "Z1")
    assert limits["maxPerDoseMl"] == {"A": 300, "B": 300, "ACID": 100}
    assert limits["minReservoirPctToDose"] == 30


def test_a_command_can_only_tighten_what_limits_json_allows():
    device = load_zone_limits(CLIENT_A, "Z1")
    looser = dose(lines=[{"line": "A", "ml": 301}], order=["A"],
                  limits={"maxPerDoseMl": {"A": 1000}, "max24hMl": {"A": 9000}})
    assert refusal(looser, ZONE, LINES, facts(device_limits=device)) == "LIMIT_DOSE"
    tighter = dose(lines=[{"line": "A", "ml": 201}], order=["A"],
                   limits={"maxPerDoseMl": {"A": 200}})
    assert refusal(tighter, ZONE, LINES, facts(device_limits=device)) == "LIMIT_DOSE"


def test_a_zone_with_no_limits_is_a_clear_error():
    with pytest.raises(ValueError, match="Z9"):
        load_zone_limits(CLIENT_A, "Z9")


@pytest.mark.parametrize(
    ("device_a", "command_a", "ml", "code"),
    [
        (100, 300, 100, None),  # device stricter: at its limit
        (100, 300, 101, "LIMIT_DOSE"),
        (300, 100, 100, None),  # command stricter: at its limit
        (300, 100, 101, "LIMIT_DOSE"),
    ],
)
def test_refusal_uses_the_stricter_limit(device_a, command_a, ml, code):
    device = {**LIMITS, "maxPerDoseMl": {"A": device_a}}
    cmd = dose(lines=[{"line": "A", "ml": ml}], order=["A"],
               limits={"maxPerDoseMl": {"A": command_a}})
    assert refusal(cmd, ZONE, LINES, facts(device_limits=device)) == code


# --- rolling 24 h ---------------------------------------------------------------------------------


def test_the_24h_total_is_rolling_and_per_line():
    now = 100 * checks.DAY_MS
    history = [
        [now - checks.DAY_MS - 1, "A", 1000],  # just over 24 h ago: gone
        [now - checks.DAY_MS, "A", 500],  # exactly 24 h ago: gone
        [now - checks.DAY_MS + 1, "A", 300],  # just inside
        [now, "A", 20],
        [now - 5, "B", 7],
    ]
    assert checks.dosed_in_last_24h(history, now) == {"A": 320, "B": 7}


def test_an_empty_history_has_no_totals():
    assert checks.dosed_in_last_24h([], 5) == {}


# --- contract time --------------------------------------------------------------------------------


def test_contract_time_parses_to_milliseconds():
    assert checks.parse_utc_ms("1970-01-01T00:00:01.500Z") == 1500


@pytest.mark.parametrize("text", [None, 5, "", "soon", "2026-11-09", "2026-11-09T04:56:00+05:30"])
def test_anything_else_is_not_contract_time(text):
    assert checks.parse_utc_ms(text) is None


# --- the order: contract 9 rule 1 -----------------------------------------------------------------


ALL_FAILING = dict(
    estop=True,
    latches=("LEAK",),
    stopped=True,
    mode="LOCAL",
    busy=True,
    ec=(None, "FAULT"),
    dosed_24h_ml={"A": 1500},
    day_tanks_ml={"A": 0.0},
    reservoir_pct=(1, "OK"),
)
# Taking away one failure at a time, the next rule in the contract's list must answer.
ORDER = [
    ("ESTOP", {"estop": False}),
    ("FAULT_LATCHED", {"latches": ()}),
    ("ZONE_STOPPED", {"stopped": False}),
    ("NOT_REMOTE", {"mode": "REMOTE"}),
    ("BUSY", {"busy": False}),
    ("SENSOR_STALE", {"ec": OK}),
]


def test_the_checks_run_in_the_contracts_order():
    over_everything = dose(lines=[{"line": "A", "ml": 400}], order=["A"])
    current = dict(ALL_FAILING)
    for code, clear in ORDER:
        assert refusal(over_everything, ZONE, LINES, facts(**current)) == code
        current.update(clear)
    assert refusal(over_everything, ZONE, LINES, facts(**current)) == "LIMIT_DOSE"
    within_per_dose = dose(lines=[{"line": "A", "ml": 200}], order=["A"])
    assert refusal(within_per_dose, ZONE, LINES, facts(**current)) == "LIMIT_24H"
    current["dosed_24h_ml"] = {}
    assert refusal(within_per_dose, ZONE, LINES, facts(**current)) == "DAYTANK_LOW"
    current["day_tanks_ml"] = {"A": 32000.0}
    assert refusal(within_per_dose, ZONE, LINES, facts(**current)) == "RESERVOIR_LOW"
    current["reservoir_pct"] = (78, "OK")
    assert refusal(within_per_dose, ZONE, LINES, facts(**current)) is None


def test_invalid_comes_before_expired_and_expired_before_everything_else():
    late = "2026-11-09T04:55:00.000Z"
    assert refusal(dose(lines=None, expiresAt=late), ZONE, LINES,
                   facts(**ALL_FAILING)) == "INVALID_COMMAND"
    assert refusal(dose(expiresAt=late), ZONE, LINES, facts(**ALL_FAILING)) == "EXPIRED_ON_ARRIVAL"


def test_circulation_comes_before_invalid():
    cmd = other("ANYTHING", {"equipment": "Z1-CIRC"})
    assert refusal(cmd, ZONE, LINES, facts()) == "CIRC_PROTECTED"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"estop": True}, "ESTOP"),
        ({"latches": ("LEAK",)}, "FAULT_LATCHED"),
        ({"stopped": True}, "ZONE_STOPPED"),
        ({"mode": "MAINT"}, "NOT_REMOTE"),
        ({"busy": True}, "BUSY"),
    ],
)
def test_a_refill_start_goes_through_the_start_checks(changes, code):
    assert refusal(refill(), ZONE, LINES, facts()) is None
    assert refusal(refill(), ZONE, LINES, facts(**changes)) == code


def test_a_refill_doesnt_need_the_dosing_checks():
    bad_for_dosing = dict(ec=(None, "FAULT"), reservoir_pct=(1, "OK"), day_tanks_ml={})
    assert refusal(refill(), ZONE, LINES, facts(**bad_for_dosing)) is None


def test_resume_is_refused_only_by_estop_and_latches():
    resume = other("RESUME")
    assert refusal(resume, ZONE, LINES, facts(estop=True)) == "ESTOP"
    assert refusal(resume, ZONE, LINES, facts(latches=("LEAK",))) == "FAULT_LATCHED"
    still_fine = dict(stopped=True, mode="LOCAL", busy=True, ec=(None, "FAULT"))
    assert refusal(resume, ZONE, LINES, facts(**still_fine)) is None


def test_stop_is_refused_only_when_it_has_expired():
    stop = other("STOP", {"scope": "ALL_OPERATIONS"})
    assert refusal(stop, ZONE, LINES, facts(**ALL_FAILING)) is None
    late = other("STOP", {"scope": "ALL_OPERATIONS"}, expiresAt="2026-11-09T04:55:00.000Z")
    assert refusal(late, ZONE, LINES, facts()) == "EXPIRED_ON_ARRIVAL"


def test_clear_fault_goes_through_under_estop():
    clear = other("CLEAR_FAULT", fault="LEAK")
    assert refusal(clear, ZONE, LINES, facts(**ALL_FAILING)) is None


def test_a_day_tank_with_no_level_sensor_by_config_is_left_to_a_person():
    """T1-32: the rig's drums have no sensors (unmonitored_tanks); a fitted one still counts."""
    assert checks.check_day_tanks({"A": 50}, {}, unmonitored={"A"}) is None
    assert checks.check_day_tanks({"A": 50}, {"A": None}, unmonitored={"B"}) == "DAYTANK_LOW"
    assert refusal(dose(lines=[{"line": "A", "ml": 50}], order=["A"]), ZONE, LINES,
                   facts(day_tanks_ml={}, unmonitored_tanks={"A"})) is None


def test_a_zone_that_fills_itself_refuses_refill_with_feature_off():
    assert refusal(refill(), ZONE, LINES, facts(refill_available=False)) == "FEATURE_OFF"
    assert refusal(refill(), ZONE, LINES, facts()) is None
