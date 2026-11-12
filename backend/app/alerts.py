"""Alerts (contract §6.5, §11.2, T1-24): what the back end raises from the farm's messages.

Two sources, both in the worker, in the same transaction that stores the message:

- **Rules on readings.** Each new ZONE_TELEMETRY is checked against the limits in alerts.json:
  EC above `zones.{zone}.alarms.ecMaxMsCm`, solution above `solTempMaxC`, circulation below
  `circFlowMinLpm`, reservoir below `events.RESERVOIR_LOW.thresholdPct` and a day-tank below
  `events.DAYTANK_LOW.thresholdPct`. An alert is raised once the condition has lasted the
  kind's `delayS` (real time, §11.4), and cleared when a reading is back within the limit.
  Only a reading marked OK counts: a faulty or stale one says nothing about the water.
- **Events.** An EVENT (§6.5) that is CRITICAL or WARNING raises an alert with its own
  message, systemAction and nextStep (PRD rule 7). Its pair clears it: ESTOP_RELEASED clears
  ESTOP_PRESSED, LEAK_CLEARED clears LEAK_DETECTED, and so on; MODE_CHANGED back to REMOTE
  clears MODE_CHANGED. Other INFO events raise nothing.

One alert per condition: `key` names it (kind, zone and line, metric or equipment), and while an
alert with that key is not cleared, no second one is raised. Acknowledging records who saw it;
it doesn't clear it. A playground reset clears every alert (POST /api/sim/reset, T1-25).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .contract import Envelope, Event, ZoneTelemetry
from .db.models import Alert

# An event that clears the alert another event raised (§6.5).
CLEARS = {
    "ESTOP_RELEASED": "ESTOP_PRESSED",
    "LEAK_CLEARED": "LEAK_DETECTED",
    "CIRC_FLOW_RESTORED": "CIRC_NO_FLOW",
    "SENSOR_RECOVERED": "SENSOR_FAULT",
}
DETAIL_KEYS = ("line", "metric", "equipment")  # what tells two alerts of one kind apart (§6.5)


@dataclass(frozen=True)
class ZoneLimits:
    name: str  # the zone's name from layout.json, e.g. Zone A
    ec_max: float | None
    sol_temp_max: float | None
    circ_flow_min: float | None


@dataclass(frozen=True)
class AlertRules:
    """The limits and delays in alerts.json, with the zone names from layout.json."""

    zones: Mapping[str, ZoneLimits]
    reservoir_low_pct: float | None
    daytank_low_pct: float | None
    delay_s: Mapping[str, float]  # by kind, from alerts.json events.{kind}.delayS

    def delay(self, kind: str) -> timedelta:
        return timedelta(seconds=self.delay_s.get(kind, 0) or 0)

    @classmethod
    def from_pack(cls, client_dir: Path) -> "AlertRules":
        from .api.pack import _read_json, read_farm  # the API's pack reader, without its routes

        path = client_dir / "alerts.json"
        if not path.exists():  # a pack without alerts.json raises no alerts
            return cls(zones={}, reservoir_low_pct=None, daytank_low_pct=None, delay_s={})
        alerts = _read_json(path)
        events = alerts.get("events") or {}
        zones = {}
        for zone in read_farm(client_dir).zones:
            limits = ((alerts.get("zones") or {}).get(zone.id) or {}).get("alarms") or {}
            zones[zone.id] = ZoneLimits(
                zone.name, limits.get("ecMaxMsCm"), limits.get("solTempMaxC"), limits.get("circFlowMinLpm")
            )
        return cls(
            zones=zones,
            reservoir_low_pct=(events.get("RESERVOIR_LOW") or {}).get("thresholdPct"),
            daytank_low_pct=(events.get("DAYTANK_LOW") or {}).get("thresholdPct"),
            delay_s={kind: spec.get("delayS") or 0 for kind, spec in events.items() if isinstance(spec, dict)},
        )


@dataclass(frozen=True)
class Condition:
    """One rule's verdict on one reading: in alarm or not, and the words for its alert."""

    kind: str
    key: str
    on: bool
    severity: str
    message: str
    system_action: str
    next_step: str


def _number(value: float) -> str:
    return f"{value:g}"


NO_ACTION = "No automatic action: the farm keeps running as it was"


def zone_conditions(message: ZoneTelemetry, rules: AlertRules) -> list[Condition]:
    """Every rule that has a limit and an OK reading to judge, for this zone's telemetry."""
    limits = rules.zones.get(message.zone)
    if limits is None:
        return []
    readings = message.readings.model_dump()
    quality = message.quality
    zone = message.zone
    found: list[Condition] = []

    def ok(name: str) -> float | None:
        value = readings.get(name)
        return value if quality.get(name) == "OK" and isinstance(value, (int, float)) else None

    if limits.ec_max is not None and (ec := ok("ec")) is not None:
        found.append(Condition(
            "EC_HIGH", f"EC_HIGH:{zone}", ec > limits.ec_max, "WARNING",
            f"EC in {limits.name} is {_number(ec)} mS/cm, above the {_number(limits.ec_max)} alarm limit",
            NO_ACTION,
            "Don't add nutrients; dilute with fresh water and check the EC probe",
        ))
    if limits.sol_temp_max is not None and (temp := ok("solTempC")) is not None:
        found.append(Condition(
            "SOL_TEMP_HIGH", f"SOL_TEMP_HIGH:{zone}", temp > limits.sol_temp_max, "WARNING",
            f"Solution in {limits.name} is {_number(temp)} °C, above the "
            f"{_number(limits.sol_temp_max)} °C alarm limit",
            NO_ACTION,
            "Check the chillers and shading; warm solution holds less oxygen for the roots",
        ))
    if limits.circ_flow_min is not None and (flow := ok("circFlowLpm")) is not None:
        found.append(Condition(
            "CIRC_NO_FLOW", f"CIRC_NO_FLOW:{zone}", flow < limits.circ_flow_min, "CRITICAL",
            f"Circulation in {limits.name} is {_number(flow)} L/min, below "
            f"{_number(limits.circ_flow_min)} L/min",
            NO_ACTION,
            "Check the circulation pump and channels: roots dry out without flow",
        ))
    if rules.reservoir_low_pct is not None and (level := ok("reservoirPct")) is not None:
        found.append(Condition(
            "RESERVOIR_LOW", f"RESERVOIR_LOW:{zone}", level < rules.reservoir_low_pct, "WARNING",
            f"{limits.name} reservoir is at {_number(level)} %, below {_number(rules.reservoir_low_pct)} %",
            NO_ACTION,
            "Refill the reservoir",
        ))
    if rules.daytank_low_pct is not None and quality.get("dayTankPct") == "OK":
        for line, level in (readings.get("dayTankPct") or {}).items():
            if isinstance(level, (int, float)):
                found.append(Condition(
                    "DAYTANK_LOW", f"DAYTANK_LOW:{zone}:{line}", level < rules.daytank_low_pct, "WARNING",
                    f"Day-tank {line} in {limits.name} is at {_number(level)} %, below "
                    f"{_number(rules.daytank_low_pct)} %",
                    NO_ACTION,
                    f"Top up day-tank {line} and record the refill",
                ))
    return found


def _open(connection: Connection, org_id: str, site_id: str, key: str) -> Any:
    """The alert with this key that isn't cleared yet (raised or still waiting out its delay)."""
    return connection.execute(
        select(Alert.alert_id, Alert.raised_at, Alert.first_seen_at).where(
            Alert.org_id == org_id, Alert.site_id == site_id, Alert.key == key, Alert.cleared_at.is_(None)
        )
    ).first()


def _raise(
    connection: Connection, org_id: str, site_id: str, zone: str | None, condition: Condition,
    now: datetime, sim_ts: datetime | None, raised: bool,
) -> None:
    connection.execute(insert(Alert).values(
        org_id=org_id, site_id=site_id, alert_id=str(uuid.uuid4()), key=condition.key,
        kind=condition.kind, severity=condition.severity, zone_id=zone,
        message=condition.message, system_action=condition.system_action, next_step=condition.next_step,
        first_seen_at=now, raised_at=now if raised else None, sim_ts=sim_ts,
    ))


def _clear(connection: Connection, org_id: str, site_id: str, key: str, now: datetime) -> None:
    """The condition is over: a raised alert is cleared, one still waiting out its delay goes."""
    found = _open(connection, org_id, site_id, key)
    if found is None:
        return
    if found.raised_at is None:
        connection.execute(delete(Alert).where(Alert.alert_id == found.alert_id))
    else:
        connection.execute(update(Alert).where(Alert.alert_id == found.alert_id).values(cleared_at=now))


def judge(
    connection: Connection, org_id: str, site_id: str, zone: str | None, condition: Condition,
    delay: timedelta, now: datetime, sim_ts: datetime | None,
) -> None:
    """Raise, keep or clear the alert for one condition."""
    if not condition.on:
        _clear(connection, org_id, site_id, condition.key, now)
        return
    found = _open(connection, org_id, site_id, condition.key)
    if found is None:
        _raise(connection, org_id, site_id, zone, condition, now, sim_ts, raised=delay <= timedelta(0))
    elif found.raised_at is None and now - found.first_seen_at >= delay:
        connection.execute(update(Alert).where(Alert.alert_id == found.alert_id).values(raised_at=now))


def _event_key(kind: str, event: Event) -> str:
    detail = event.detail or {}
    extra = next((str(detail[k]) for k in DETAIL_KEYS if detail.get(k) is not None), "")
    return ":".join(part for part in (kind, event.zone or event.device, extra) if part)


def on_event(connection: Connection, org_id: str, event: Event, now: datetime) -> None:
    site_id = event.site
    if event.kind in CLEARS:
        _clear(connection, org_id, site_id, _event_key(CLEARS[event.kind], event), now)
        return
    if event.kind == "MODE_CHANGED" and (event.detail or {}).get("to") == "REMOTE":
        _clear(connection, org_id, site_id, _event_key("MODE_CHANGED", event), now)
        return
    if event.severity == "INFO":
        return
    condition = Condition(
        event.kind, _event_key(event.kind, event), True, event.severity,
        event.message, event.system_action, event.next_step,
    )
    if _open(connection, org_id, site_id, condition.key) is None:
        _raise(connection, org_id, site_id, event.zone, condition, now, event.sim_ts, raised=True)


def on_message(
    connection: Connection, org_id: str, message: Envelope, rules: AlertRules, now: datetime
) -> None:
    """What one new message changes in the alerts. Call it only for a message that is new (not a
    replay) and, for telemetry, the newest of its kind, so an old reading can't undo a new one."""
    if isinstance(message, Event):
        on_event(connection, org_id, message, now)
    elif isinstance(message, ZoneTelemetry):
        for condition in zone_conditions(message, rules):
            judge(
                connection, org_id, message.site, message.zone, condition,
                rules.delay(condition.kind), now, message.sim_ts,
            )


def clear_all(connection: Connection, org_id: str, site_id: str, now: datetime) -> None:
    """A playground reset: every alert is cleared, and any still waiting out its delay goes."""
    the_site = (Alert.org_id == org_id, Alert.site_id == site_id, Alert.cleared_at.is_(None))
    connection.execute(delete(Alert).where(*the_site, Alert.raised_at.is_(None)))
    connection.execute(update(Alert).where(*the_site).values(cleared_at=now))
