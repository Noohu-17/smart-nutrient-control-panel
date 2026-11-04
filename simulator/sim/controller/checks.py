"""A zone controller's own safety checks (contract 9 rule 1), as plain functions.

Each check takes only what it needs and returns a reason code (contract 8.4), or None if the
command passes. Nothing here touches the HAL or keeps any state, so every rule can be tested alone
and ported to C++ line for line. refusal() runs them in the contract's order, and the first that
fails is the reason reported. It never trusts that the cloud checked (contract 9 rule 1).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .hal import Reading

ML_PER_L = 1000.0
DAY_MS = 24 * 60 * 60 * 1000
REFILL_MAX_RUN_S = (60, 3600)  # contract 7.3: 1-60 minutes
STOP_SCOPES = ("ALL_OPERATIONS", "ZONE", "ALL_EQUIPMENT")  # contract 7.4


@dataclass(frozen=True)
class Facts:
    """What the controller knows at the moment a command arrives."""

    now_utc_ms: int
    estop: bool
    latches: Collection[str]
    stopped: bool
    mode: str
    busy: bool
    ec: Reading
    ph: Reading
    reservoir_pct: Reading
    day_tanks_ml: Mapping[str, float | None]  # None: its level sensor can't be trusted
    dosed_24h_ml: Mapping[str, int]
    device_limits: Mapping[str, Any]


# --- the rules, one by one ------------------------------------------------------------


def check_circulation(cmd: Mapping[str, Any], zone_id: str) -> str | None:
    """No command may switch circulation (contract 2.3, 9 rule 4)."""
    target = cmd.get("target")
    equipment = target.get("equipment") if isinstance(target, Mapping) else None
    return "CIRC_PROTECTED" if equipment == f"{zone_id}-CIRC" else None


def check_fields(cmd: Mapping[str, Any], zone_id: str, dose_lines: Collection[str]) -> str | None:
    """A schema or value error (contract 8.4)."""
    fields_ok = _FIELD_CHECKS.get(cmd.get("kind"))
    if fields_ok is None or parse_utc_ms(cmd.get("expiresAt")) is None:
        return "INVALID_COMMAND"
    return None if fields_ok(cmd, zone_id, dose_lines) else "INVALID_COMMAND"


def check_expiry(expires_at_ms: int, now_utc_ms: int) -> str | None:
    """Arriving at or after expiresAt is too late (contract 8.3 rule 2)."""
    return "EXPIRED_ON_ARRIVAL" if now_utc_ms >= expires_at_ms else None


def check_estop(pressed: bool) -> str | None:
    return "ESTOP" if pressed else None


def check_latches(latches: Collection[str]) -> str | None:
    return "FAULT_LATCHED" if latches else None


def check_stopped(stopped: bool) -> str | None:
    """Stopped stays stopped until a person sends RESUME (contract 8.3 rule 8)."""
    return "ZONE_STOPPED" if stopped else None


def check_remote(mode: str) -> str | None:
    """Commands from the app work only with the key switch on REMOTE (contract 0)."""
    return None if mode == "REMOTE" else "NOT_REMOTE"


def check_idle(busy: bool) -> str | None:
    """One operation per zone (contract 8.3 rule 4)."""
    return "BUSY" if busy else None


def check_ec_ph(ec: Reading, ph: Reading) -> str | None:
    """EC or pH that is not OK is never used for dosing (contract 6.1); a stale one isn't OK."""
    return None if _is_ok(ec) and _is_ok(ph) else "SENSOR_STALE"


def check_per_dose(requests: Mapping[str, int], limits: Mapping[str, Any]) -> str | None:
    """No line over its per-dose maximum. A line with no limit at all can't be shown safe."""
    allowed = limits.get("maxPerDoseMl") or {}
    for line, ml in requests.items():
        if ml > 0 and (line not in allowed or ml > allowed[line]):
            return "LIMIT_DOSE"
    return None


def check_24h(
    requests: Mapping[str, int], limits: Mapping[str, Any], dosed_ml: Mapping[str, int]
) -> str | None:
    """A line's rolling 24 h total, from the controller's own counters (contract 9 rule 8)."""
    allowed = limits.get("max24hMl") or {}
    for line, ml in requests.items():
        if ml > 0 and (line not in allowed or dosed_ml.get(line, 0) + ml > allowed[line]):
            return "LIMIT_24H"
    return None


def check_day_tanks(
    requests: Mapping[str, int], tanks_ml: Mapping[str, float | None]
) -> str | None:
    """Enough concentrate for every line. A level that can't be trusted isn't enough."""
    for line, ml in requests.items():
        held = tanks_ml.get(line)
        if ml > 0 and (held is None or ml > held):
            return "DAYTANK_LOW"
    return None


def check_reservoir(reservoir_pct: Reading, limits: Mapping[str, Any]) -> str | None:
    """Enough water to dose into. A level that can't be trusted, or no minimum, isn't enough."""
    minimum = limits.get("minReservoirPctToDose")
    if minimum is None or not _is_ok(reservoir_pct) or reservoir_pct[0] < minimum:
        return "RESERVOIR_LOW"
    return None


def stricter_limits(device: Mapping[str, Any], command: Mapping[str, Any] | None) -> dict[str, Any]:
    """The stricter of the controller's own limits and the command's snapshot (contract 7.2)."""
    command = command or {}
    minimums = [
        limits["minReservoirPctToDose"]
        for limits in (device, command)
        if limits.get("minReservoirPctToDose") is not None
    ]
    return {
        "maxPerDoseMl": _lowest(device.get("maxPerDoseMl"), command.get("maxPerDoseMl")),
        "max24hMl": _lowest(device.get("max24hMl"), command.get("max24hMl")),
        "minReservoirPctToDose": max(minimums) if minimums else None,
    }


def dosed_in_last_24h(history: Iterable[Iterable[Any]], now_ms: int) -> dict[str, int]:
    """Delivered mL per line in the rolling 24 hours before now (contract 9 rule 8)."""
    totals: dict[str, int] = {}
    for at_ms, line, ml in history:
        if now_ms - at_ms < DAY_MS:
            totals[line] = totals.get(line, 0) + ml
    return totals


def parse_utc_ms(text: Any) -> int | None:
    """Milliseconds since the epoch from contract time, or None if it isn't contract time."""
    if not isinstance(text, str) or not text.endswith("Z"):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return round(moment.astimezone(UTC).timestamp() * 1000)


# --- the order they run in ---------------------------------------------------------------


def refusal(
    cmd: Mapping[str, Any], zone_id: str, dose_lines: Collection[str], facts: Facts
) -> str | None:
    """Why this command must be refused, or None. The order is contract 9 rule 1's.

    A known cmdId comes before all of these and belongs to the controller: it has the memory.
    Circulation is not in the contract's list; it comes first, as a command aimed at it would
    otherwise be refused as invalid instead of as protected.
    """
    reason = check_circulation(cmd, zone_id) or check_fields(cmd, zone_id, dose_lines)
    if reason:
        return reason
    reason = check_expiry(parse_utc_ms(cmd["expiresAt"]), facts.now_utc_ms)
    if reason:
        return reason
    kind = cmd["kind"]
    if kind == "DOSE":
        return _dose_refusal(cmd, facts)
    if kind == "REFILL":
        return _start_refusal(facts)
    if kind == "RESUME":  # contract 7.8
        return check_estop(facts.estop) or check_latches(facts.latches)
    return None  # STOP is always allowed (contract 7.4), and a person may clear a repaired fault


def _start_refusal(facts: Facts) -> str | None:
    return (
        check_estop(facts.estop)
        or check_latches(facts.latches)
        or check_stopped(facts.stopped)
        or check_remote(facts.mode)
        or check_idle(facts.busy)
    )


def _dose_refusal(cmd: Mapping[str, Any], facts: Facts) -> str | None:
    requests = {item["line"]: item["ml"] for item in cmd["lines"]}
    limits = stricter_limits(facts.device_limits, cmd.get("limits"))
    return (
        _start_refusal(facts)
        or check_ec_ph(facts.ec, facts.ph)
        or check_per_dose(requests, limits)
        or check_24h(requests, limits, facts.dosed_24h_ml)
        or check_day_tanks(requests, facts.day_tanks_ml)
        or check_reservoir(facts.reservoir_pct, limits)
    )


# --- what a well-formed command looks like (contract 7) -----------------------------------


def _targets_zone(cmd: Mapping[str, Any], zone_id: str) -> bool:
    target = cmd.get("target")
    return isinstance(target, Mapping) and target.get("zone") == zone_id


def _dose_fields_ok(cmd: Mapping[str, Any], zone_id: str, dose_lines: Collection[str]) -> bool:
    lines, order = cmd.get("lines"), cmd.get("order")
    if not _targets_zone(cmd, zone_id) or not isinstance(lines, list) or not lines:
        return False
    if not all(_line_ok(item, dose_lines) for item in lines):
        return False
    names = [item["line"] for item in lines]
    if len(set(names)) != len(names) or not isinstance(order, list):
        return False
    if sorted(map(str, order)) != sorted(names):
        return False
    if not any(item["ml"] > 0 for item in lines):
        return False  # a dose of nothing is a value error
    return _limits_ok(cmd.get("limits"))


def _line_ok(item: Any, dose_lines: Collection[str]) -> bool:
    if not isinstance(item, Mapping) or item.get("line") not in dose_lines:
        return False
    return _is_whole(item.get("ml")) and item["ml"] >= 0  # volumes are whole mL (contract 1)


def _limits_ok(limits: Any) -> bool:
    if limits is None:
        return True
    if not isinstance(limits, Mapping):
        return False
    for key in ("maxPerDoseMl", "max24hMl"):
        per_line = limits.get(key, {})
        if not isinstance(per_line, Mapping) or not all(map(_is_number, per_line.values())):
            return False
    return True


def _refill_fields_ok(cmd: Mapping[str, Any], zone_id: str, _: Collection[str]) -> bool:
    """START only: PAUSE, RESUME and STOP act on a running refill, which comes with execution."""
    if not _targets_zone(cmd, zone_id) or cmd.get("action") != "START":
        return False
    target_pct, run_s = cmd.get("targetPct"), cmd.get("maxDurationS")
    if not _is_number(target_pct) or not 0 < target_pct <= 100:
        return False
    return _is_number(run_s) and REFILL_MAX_RUN_S[0] <= run_s <= REFILL_MAX_RUN_S[1]


def _stop_fields_ok(cmd: Mapping[str, Any], _: str, __: Collection[str]) -> bool:
    target = cmd.get("target")
    if not isinstance(target, Mapping) or target.get("scope") not in STOP_SCOPES:
        return False
    return target["scope"] != "ZONE" or isinstance(target.get("zone"), str)


def _resume_fields_ok(cmd: Mapping[str, Any], zone_id: str, _: Collection[str]) -> bool:
    return _targets_zone(cmd, zone_id)


def _clear_fault_fields_ok(cmd: Mapping[str, Any], zone_id: str, _: Collection[str]) -> bool:
    fault = cmd.get("fault")
    return _targets_zone(cmd, zone_id) and isinstance(fault, str) and bool(fault)


_FIELD_CHECKS: dict[Any, Callable[[Mapping[str, Any], str, Collection[str]], bool]] = {
    "DOSE": _dose_fields_ok,
    "REFILL": _refill_fields_ok,
    "STOP": _stop_fields_ok,
    "RESUME": _resume_fields_ok,
    "CLEAR_FAULT": _clear_fault_fields_ok,
}


def _is_ok(reading: Reading) -> bool:
    return reading[0] is not None and reading[1] == "OK"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _lowest(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> dict[str, Any]:
    first, second = first or {}, second or {}
    return {
        line: min(limits[line] for limits in (first, second) if line in limits)
        for line in {*first, *second}
    }
