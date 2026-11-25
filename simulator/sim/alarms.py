"""Alarm thresholds, and when crossing one calls for a reading out of turn (contract 6.1, 6.2).

Every limit comes from the config pack. A reading out of turn goes out when a value crosses a
limit, in either direction, not for as long as it stays beyond it. A value that keeps crossing
the same limit gets one such reading per reading period; the next periodic reading reports
where it ended up, so a noisy value can't flood the broker and nothing goes unreported.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .model.params import require_finite


@dataclass(frozen=True, slots=True)
class Alarm:
    """One limit on one reading, named as in the message: "solTempC", "dayTankPct.A"."""

    reading: str
    limit: float
    above: bool  # True: in alarm above the limit; False: in alarm below it

    def breached_by(self, value: float | None) -> bool:
        """A null reading is no measurement: its quality reports that, not an alarm."""
        if value is None:
            return False
        return value > self.limit if self.above else value < self.limit


def zone_alarms(alerts: Mapping[str, Any], zone_id: str, lines: Iterable[str]) -> tuple[Alarm, ...]:
    """A zone's limits from alerts.json; a limit that isn't configured raises no alarm."""
    zone_cfg = (alerts.get("zones") or {}).get(zone_id) or {}
    limits = zone_cfg.get("alarms") or {}
    events = alerts.get("events") or {}
    day_tank_low = (events.get("DAYTANK_LOW") or {}).get("thresholdPct")
    return _alarms(
        [
            ("solTempC", limits.get("solTempMaxC"), True),
            ("circFlowLpm", limits.get("circFlowMinLpm"), False),
            ("reservoirPct", (events.get("RESERVOIR_LOW") or {}).get("thresholdPct"), False),
            *((f"dayTankPct.{line}", day_tank_low, False) for line in lines),
        ]
    )


def climate_alarms(setpoints: Mapping[str, Any]) -> tuple[Alarm, ...]:
    """Climate limits: alerts.json has none, so the air's are climate.json's setpoints."""
    return _alarms(
        [
            ("airTempC", setpoints.get("airTempMaxC"), True),
            ("airRh", setpoints.get("airRhMax"), True),
        ]
    )


def _alarms(limits: Iterable[tuple[str, Any, bool]]) -> tuple[Alarm, ...]:
    alarms = []
    for reading, limit, above in limits:
        if limit is not None:
            require_finite(f"alarm limit for {reading}", limit)
            alarms.append(Alarm(reading, limit, above))
    return tuple(alarms)


class AlarmWatch:
    """Compares a device's readings with the alarms as they stood in its last reading."""

    def __init__(self, alarms: Iterable[Alarm]):
        self._alarms = tuple(alarms)
        self._reported: frozenset[str] = frozenset()
        self._crossed_this_period: set[str] = set()

    def crossed(self, readings: Mapping[str, Any]) -> bool:
        """True if a reading should go out now; `readings` as in the message."""
        changed = self._breached(readings) ^ self._reported
        return bool(changed - self._crossed_this_period)

    def reported(self, readings: Mapping[str, Any], *, periodic: bool) -> None:
        """Note the readings just sent. A periodic reading starts a new period."""
        breached = self._breached(readings)
        if periodic:
            self._crossed_this_period.clear()
        else:
            self._crossed_this_period |= breached ^ self._reported
        self._reported = breached

    def _breached(self, readings: Mapping[str, Any]) -> frozenset[str]:
        flat = _flatten(readings)
        return frozenset(a.reading for a in self._alarms if a.breached_by(flat.get(a.reading)))


def _flatten(readings: Mapping[str, Any]) -> dict[str, Any]:
    """{"dayTankPct": {"A": 64}} becomes {"dayTankPct.A": 64}."""
    flat: dict[str, Any] = {}
    for name, value in readings.items():
        if isinstance(value, Mapping):
            flat.update({f"{name}.{key}": item for key, item in value.items()})
        else:
            flat[name] = value
    return flat
