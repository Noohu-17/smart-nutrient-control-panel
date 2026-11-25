"""The dose review's safety checks (contract §11.3), as one plain function.

The check IDs, their order and their messages are the app's fake (app/src/mocks/dosing.ts, T2-11),
so the app reads the same answer from either. Where the fake can't know something the server can
(no ZONE_STATE yet, a stopped zone, a last will), the server says so in the same style.

Unknown means blocked (§8.3 rule 7): a state field that is still null never passes. Nothing here
touches the database or the farm; the route gathers the facts and saves the answer.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .pack import ZoneDosing
from .schemas import DoseCheck, DoseEstimate, DoseLineAmount, FarmZone, Projected, ZoneStatus
from .status import FRESH_FOR, OFFLINE_AFTER

Result = Literal["PASS", "WARN", "BLOCK"]

BUSY_EQUIPMENT = ("-DOSE", "-REFILL")  # Z1-DOSE-A, Z1-REFILL (§2.3)


class DoseInputError(ValueError):
    """Amounts that can't be a dose at all: a 422 with INVALID_COMMAND, as in the fake."""


@dataclass(frozen=True)
class Review:
    projected: Projected
    checks: list[DoseCheck]
    estimate: DoseEstimate

    @property
    def can_confirm(self) -> bool:
        return not any(check.result == "BLOCK" for check in self.checks)


# What a reading's quality (§6.1) means, for the review's message.
QUALITY_TEXT = {
    "FAULT": "sensor fault",
    "OUT_OF_RANGE": "outside the sensor's range",
    "UNCALIBRATED": "sensor needs calibrating",
}


def _unusable_why(quality: str, value: float | None) -> str:
    if quality in QUALITY_TEXT:
        return QUALITY_TEXT[quality]
    if quality == "missing":
        return "it came without a quality mark" if value is not None else "no reading"
    return "no value" if value is None else f"marked {quality}"


def validate_lines(zone: FarmZone, lines: Sequence[DoseLineAmount]) -> None:
    """Whole, non-negative mL on lines the zone has, each once, and at least one above zero (§1)."""
    seen: set[str] = set()
    for amount in lines:
        if amount.line not in zone.dose_lines:
            raise DoseInputError(f"{zone.name} has no dosing line {amount.line}")
        if amount.line in seen:
            raise DoseInputError(f"Line {amount.line} is given more than once")
        seen.add(amount.line)
        if not float(amount.ml).is_integer() or amount.ml < 0:
            raise DoseInputError(f"Line {amount.line}: enter whole mL")
    if not any(amount.ml > 0 for amount in lines):
        raise DoseInputError("Enter an amount for at least one line")


def _round(value: float, places: int) -> float:
    """Half away from zero, as the fake's Math.round does for these positive values."""
    scale = 10**places
    return math.floor(value * scale + 0.5) / scale


def _number(value: float) -> str:
    """A number as JavaScript prints it: 300, not 300.0."""
    return str(int(value)) if float(value).is_integer() else str(value)


def _seconds_since(moment: datetime | None, now: datetime) -> int | None:
    return None if moment is None else round((now - moment).total_seconds())


def review(
    zone: FarmZone,
    status: ZoneStatus,
    dosing: ZoneDosing,
    lines: Sequence[DoseLineAmount],
    dosed_24h_ml: Mapping[str, float],
    now: datetime,
) -> Review:
    """Run every check in the §11.3 order. `status` is the zone's GET /zones/{zoneId} answer."""
    checks: list[DoseCheck] = []

    def add(check_id: str, ok: bool, passed: str, failed: str, fail_result: Result = "BLOCK") -> None:
        checks.append(
            DoseCheck(id=check_id, result="PASS" if ok else fail_result, message=passed if ok else failed)
        )

    name, readings, limits = zone.name, status.readings, dosing.limits
    dosing_lines = [amount for amount in lines if amount.ml > 0]
    age_s = _seconds_since(status.ts, now)
    silent_s = _seconds_since(status.last_seen, now)

    # STATE_KNOWN: a ZONE_STATE has arrived, not stopped, nothing unconfirmed, no latched fault
    if status.stopped is None:
        add("STATE_KNOWN", False, "", f"{name} state not known yet")
    elif status.stopped:
        add("STATE_KNOWN", False, "", f"{name} is stopped. Resume the zone first")
    elif status.active_cmd_id is not None:
        add("STATE_KNOWN", False, "", f"{name} has a command still in flight")
    elif status.latches:
        add("STATE_KNOWN", False, "", f"{name} has a latched fault to clear: {', '.join(status.latches)}")
    else:
        add("STATE_KNOWN", True, f"{name} state known: nothing unconfirmed, not stopped", "")

    if silent_s is None:
        offline = f"{zone.controller} has never reported"
    elif silent_s < OFFLINE_AFTER.total_seconds():  # heard from lately, then its last will
        offline = f"{zone.controller} has gone offline"
    else:
        offline = f"{zone.controller} not heard from for {silent_s} s"
    add("DEVICES_ONLINE", status.online, f"{zone.controller} and sensors online", offline)

    add(
        "ESTOP",
        status.estop is False,
        "No E-STOP pressed",
        f"E-STOP state of {name} not known yet" if status.estop is None else f"E-STOP pressed in {name}",
    )
    add(
        "ZONE_REMOTE",
        status.mode == "REMOTE",
        f"{name} is in REMOTE",
        f"{name} key switch is in {status.mode or 'an unknown position'}",
    )

    busy = (
        status.active_cmd_id is not None
        or status.operation is not None
        or any(
            any(part in equipment for part in BUSY_EQUIPMENT) and state in ("ON", "OPEN")
            for equipment, state in status.equipment.items()
        )
    )
    add("NO_CONFLICT", not busy, "Nothing else running", f"Another dose or refill is running in {name}")

    per_dose = limits.get("maxPerDoseMl", {})
    over_dose = [a for a in dosing_lines if a.ml > per_dose.get(a.line, 0)]
    add(
        "LIMIT_DOSE",
        not over_dose,
        "All lines within per-dose limits",
        "; ".join(f"Line {a.line} exceeds {_number(per_dose.get(a.line, 0))} mL per dose" for a in over_dose),
    )

    per_day = limits.get("max24hMl", {})
    over_24h = [a for a in dosing_lines if dosed_24h_ml.get(a.line, 0) + a.ml > per_day.get(a.line, 0)]
    add(
        "LIMIT_24H",
        not over_24h,
        "24 h totals within limits",
        "; ".join(
            f"Line {a.line} would reach {_number(dosed_24h_ml.get(a.line, 0) + a.ml)} mL of the "
            f"{_number(per_day.get(a.line, 0))} mL 24 h limit"
            for a in over_24h
        ),
    )

    # DAYTANK: not enough concentrate blocks (an unknown level counts as empty); a low one warns.
    # Drums with no level sensor (io_map.json dayTankLevelSensors false, DM-4) can't be read, so
    # the review warns instead: the grower checks them by eye. The zone controller does the same.
    tank_l, tank_pct = readings.day_tank_l, readings.day_tank_pct
    short = [a for a in dosing_lines if (tank_l.get(a.line) or 0) * 1000 < a.ml]
    low = [a for a in dosing_lines if (tank_pct.get(a.line) or 0) < dosing.day_tank_warn_pct]
    if not dosing.day_tank_sensors:
        tanks = ", ".join(a.line for a in dosing_lines)
        which = f"stock tanks {tanks}" if len(dosing_lines) > 1 else f"stock tank {tanks}"
        add("DAYTANK", False, "", f"No level sensor on {which}: check by eye before dosing", "WARN")
    elif short:
        add("DAYTANK", False, "", "; ".join(f"Day-tank {a.line} has less than {_number(a.ml)} mL" for a in short))
    else:
        add(
            "DAYTANK",
            not low,
            "Enough concentrate in every day-tank",
            "; ".join(f"Day-tank {a.line} at {_number(tank_pct.get(a.line) or 0)}%, refill soon" for a in low),
            "WARN",
        )

    reservoir_pct, minimum = readings.reservoir_pct, limits.get("minReservoirPctToDose")
    shown_pct = "--" if reservoir_pct is None else _number(reservoir_pct)
    add(
        "RESERVOIR",
        reservoir_pct is not None and minimum is not None and reservoir_pct >= minimum,
        f"{zone.reservoir.id} at {shown_pct}%",
        f"{zone.reservoir.id} at {shown_pct}%, below the {_number(minimum or 0)}% minimum to dose",
    )

    # never more than the tank holds: a level reading above it is wrong, and would make a dose
    # look smaller than it is
    tank_l = zone.reservoir.volume_l or zone.reservoir.usable_l
    litres = min(readings.reservoir_l or tank_l, tank_l)
    ec = ph = None
    if readings.ec is not None:
        added = sum(a.ml * dosing.ec_per_ml_per_l.get(a.line, 0) for a in dosing_lines)
        ec = _round(readings.ec + added / litres, 2)
    if readings.ph is not None:
        change = sum(a.ml * dosing.ph_per_ml_per_l.get(a.line, 0) for a in dosing_lines)
        ph = _round(readings.ph + change / litres, 1)
    if ec is None or ph is None:
        add("PROJECTED_EC_PH", False, "", "Cannot project EC and pH without both readings")
    else:
        projected = f"Projected EC {_number(ec)}, pH {_number(ph)}"
        target = dosing.target
        if ec > limits["ecCeiling"] or ph < limits["phMin"] or ph > limits["phMax"]:
            add(
                "PROJECTED_EC_PH",
                False,
                "",
                f"{projected} is outside the safety limits (EC up to {_number(limits['ecCeiling'])}, "
                f"pH {_number(limits['phMin'])}–{_number(limits['phMax'])})",
            )
        elif target is None:
            add("PROJECTED_EC_PH", False, "", f"{projected} (no recipe applied to {name})", "WARN")
        else:
            in_target = target.ec_min <= ec <= target.ec_max and target.ph_min <= ph <= target.ph_max
            shown = (
                f"{projected} (target {_number(target.ec_min)}–{_number(target.ec_max)}, "
                f"{_number(target.ph_min)}–{_number(target.ph_max)})"
            )
            add("PROJECTED_EC_PH", in_target, shown, shown, "WARN")

    # FRESH_READINGS: OK EC and pH no older than the fresh limit (§14). STALE is the API's own
    # mark for old or offline readings, so it is about freshness; any other quality, or none at
    # all, is not usable, as in the fake.
    fresh_s = round(FRESH_FOR.total_seconds())
    names = {"ec": "EC", "ph": "pH"}
    quality = {key: status.quality.get(key, "missing") for key in ("ec", "ph")}
    unusable = [
        key
        for key in ("ec", "ph")
        if quality[key] not in ("OK", "STALE") or getattr(readings, key) is None
    ]
    stuck = [names[key] for key in ("ec", "ph") if quality[key] == "STALE"]
    if unusable and all(quality[key] == "missing" and getattr(readings, key) is None for key in unusable):
        failed = f"No {' or '.join(names[key] for key in unusable)} reading yet"
    elif unusable:
        failed = "; ".join(
            f"{names[key]} reading not usable: {_unusable_why(quality[key], getattr(readings, key))}"
            for key in unusable
        )
    elif age_s is None:
        failed = "No EC/pH reading yet"
    elif age_s > fresh_s:
        failed = f"EC/pH {age_s} s old, more than {fresh_s} s"
    elif status.online and stuck:  # the controller marks a reading that stopped changing STALE
        failed = f"{' and '.join(stuck)} {'reading is' if len(stuck) == 1 else 'readings are'} not updating"
    else:
        failed = f"EC/pH {age_s} s old, but {zone.controller} is offline"
    fresh = not unusable and age_s is not None and age_s <= fresh_s and "STALE" not in quality.values()
    add("FRESH_READINGS", fresh, f"EC/pH {age_s} s old", failed)

    pump_min = sum(a.ml / dosing.pump_ml_per_min[a.line] for a in dosing_lines)
    return Review(
        projected=Projected(ec=ec, ph=ph),
        checks=checks,
        estimate=DoseEstimate(
            duration_min=math.ceil(pump_min + dosing.mix_wait_s / 60),
            total_ml=round(sum(a.ml for a in dosing_lines)),
        ),
    )
