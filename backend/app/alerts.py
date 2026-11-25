"""Alerts (contract §6.5, §11.2, T1-24, T1-34): what the back end raises from the farm's messages,
and from its silence.

Three sources, all in the worker. The first two run in the same transaction that stores the
message:

- **Rules on readings.** Each new ZONE_TELEMETRY is checked against the limits in alerts.json:
  EC above `zones.{zone}.alarms.ecMaxMsCm`, solution above `solTempMaxC`, circulation below
  `circFlowMinLpm`, reservoir below `events.RESERVOIR_LOW.thresholdPct` and a day-tank below
  `events.DAYTANK_LOW.thresholdPct`. An alert is raised once the condition has lasted the
  kind's `delayS` (real time, §11.4), and cleared when a reading is back within the limit.
  Only a reading marked OK counts: a faulty or stale one says nothing about the water.
  The quality itself raises two more (T1-34): SENSOR_FAULT while a reading is FAULT, cleared
  once it reads again (OK, OUT_OF_RANGE or UNCALIBRATED), and READING_STALE while the
  controller marks it STALE, cleared by anything else. Both wait out their kind's delayS.
- **Events.** An EVENT (§6.5) that is CRITICAL or WARNING raises an alert with its own
  message, systemAction and nextStep (PRD rule 7). Its pair clears it: ESTOP_RELEASED clears
  ESTOP_PRESSED, LEAK_CLEARED clears LEAK_DETECTED, and so on; MODE_CHANGED back to REMOTE
  clears MODE_CHANGED. Other INFO events raise nothing.
- **Silence** (T1-34, the worker's sweep every few seconds, worker/silence.py). DEVICE_OFFLINE
  for a device that has been heard from at least once, once its last will has come or it has
  sent nothing for `events.DEVICE_OFFLINE.thresholdS` (§14's 3 minutes if the pack gives none),
  cleared when it is heard again. READING_STALE for a zone whose controller is online but whose
  newest telemetry is older than `events.READING_STALE.thresholdS` (§14's 120 s fresh limit),
  cleared by fresh telemetry. A device never heard from is not connected yet, not offline: a
  part that isn't fitted raises nothing. For its first DEVICE_OFFLINE thresholdS the sweep only
  clears: just after the worker starts, messages are old because the worker was away.

One alert per condition: `key` names it (kind, zone and line, metric or equipment), and while an
alert with that key is not cleared, no second one is raised; a reading rule's alert takes the
words of each new reading, so it says the value now, not the one it started at. Acknowledging
records who saw it; it doesn't clear it. A playground reset clears every alert (POST /api/sim/reset, T1-25).

Web Push (T1-41): raising an alert adds it to push_outbox in the same transaction, and so does an
alert turning CRITICAL. The notifier (push/notifier.py) sends those and decides who gets them.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Connection, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .contract import Envelope, Event, ZoneTelemetry
from .db.models import Alert, PushOutbox
from .timing import FRESH_FOR, OFFLINE_AFTER

if TYPE_CHECKING:
    from .packs import Pack

# An event that clears the alert another event raised (§6.5).
CLEARS = {
    "ESTOP_RELEASED": "ESTOP_PRESSED",
    "LEAK_CLEARED": "LEAK_DETECTED",
    "CIRC_FLOW_RESTORED": "CIRC_NO_FLOW",
    "SENSOR_RECOVERED": "SENSOR_FAULT",
}
DETAIL_KEYS = ("line", "metric", "equipment")  # what tells two alerts of one kind apart (§6.5)
# §14's times (app/timing.py, as the zone API uses them), when alerts.json gives no threshold.
OFFLINE_AFTER_S = OFFLINE_AFTER.total_seconds()
FRESH_FOR_S = FRESH_FOR.total_seconds()
# A quality that means the probe is answering again (§6.1): its fault is over.
ANSWERING = ("OK", "OUT_OF_RANGE", "UNCALIBRATED")
DOSING_METRICS = ("ec", "ph")  # a dose needs these fresh and OK (§8.3 rule 7: SENSOR_STALE)
METRIC_NAMES = {  # what an alert calls a quality key (§6.1)
    "ec": "EC", "ph": "pH", "solTempC": "solution temperature", "reservoirPct": "reservoir level",
    "circFlowLpm": "circulation flow", "doMgL": "dissolved oxygen", "dayTankPct": "day-tank level",
}
DEVICE_NAMES = {  # what an alert calls a device of each kind (§2.2)
    "GATEWAY": "gateway", "CLIMATE_CONTROLLER": "climate controller", "POWER_MONITOR": "power monitor",
}


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
    device_offline_s: float = OFFLINE_AFTER_S  # events.DEVICE_OFFLINE.thresholdS
    reading_stale_s: float = FRESH_FOR_S  # events.READING_STALE.thresholdS

    def delay(self, kind: str) -> timedelta:
        return timedelta(seconds=self.delay_s.get(kind, 0) or 0)

    @classmethod
    def from_pack(cls, source: "Pack | Path") -> "AlertRules":
        """From a Pack (app.packs) or a pack folder."""
        from .api.pack import read_farm  # the API's pack reader, without its routes
        from .packs import as_pack

        pack = as_pack(source)
        alerts = pack.optional("alerts.json")
        if alerts is None:  # a pack without alerts.json raises no alerts
            return cls(zones={}, reservoir_low_pct=None, daytank_low_pct=None, delay_s={})
        events = alerts.get("events") or {}
        zones = {}
        for zone in read_farm(pack).zones:
            limits = ((alerts.get("zones") or {}).get(zone.id) or {}).get("alarms") or {}
            zones[zone.id] = ZoneLimits(
                zone.name, limits.get("ecMaxMsCm"), limits.get("solTempMaxC"), limits.get("circFlowMinLpm")
            )
        return cls(
            zones=zones,
            reservoir_low_pct=(events.get("RESERVOIR_LOW") or {}).get("thresholdPct"),
            daytank_low_pct=(events.get("DAYTANK_LOW") or {}).get("thresholdPct"),
            delay_s={kind: spec.get("delayS") or 0 for kind, spec in events.items() if isinstance(spec, dict)},
            device_offline_s=_seconds(events, "DEVICE_OFFLINE", OFFLINE_AFTER_S),
            reading_stale_s=_seconds(events, "READING_STALE", FRESH_FOR_S),
        )


def _seconds(events: Mapping[str, Any], kind: str, default: float) -> float:
    """events.{kind}.thresholdS from alerts.json, or the contract's own time (§14)."""
    value = (events.get(kind) or {}).get("thresholdS")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default
    return float(value)


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


def _duration(seconds: float) -> str:
    """180 -> "3 min", 90 -> "90 s": how long, as the alert says it."""
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g} min"
    return f"{seconds:g} s"


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
            "EC_HIGH", f"EC_HIGH:{zone}", ec > limits.ec_max, "CRITICAL",
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
    found += quality_conditions(message, limits)
    return found


def _doses(metric: str, zone: str, until: str) -> str:
    """What the farm does about a reading it can't use: a dose needs EC and pH (§8.3 rule 7)."""
    if metric in DOSING_METRICS:
        return f"Doses to {zone} are refused {until}"
    return NO_ACTION


def quality_conditions(message: ZoneTelemetry, limits: ZoneLimits) -> list[Condition]:
    """SENSOR_FAULT and READING_STALE from each reading's quality (§6.1, T1-34). The key is the
    one a controller's own SENSOR_FAULT event has (kind, zone, metric), so either can raise it,
    and SENSOR_RECOVERED or a reading that answers again clears it."""
    found: list[Condition] = []
    zone = message.zone
    for metric, quality in message.quality.items():
        what = METRIC_NAMES.get(metric, metric)
        if quality == "FAULT" or quality in ANSWERING:  # STALE says nothing about a fault
            found.append(Condition(
                "SENSOR_FAULT", f"SENSOR_FAULT:{zone}:{metric}", quality == "FAULT", "WARNING",
                f"The {what} sensor in {limits.name} reports a fault: it gives no reading",
                _doses(metric, limits.name, "until it reads again"),
                "Check the probe, its cable and its connector; clean or recalibrate it if it reads wrong",
            ))
        found.append(Condition(
            "READING_STALE", f"READING_STALE:{zone}:{metric}", quality == "STALE", "WARNING",
            f"The {what} reading in {limits.name} is stale: the sensor hasn't sent a new value",
            _doses(metric, limits.name, "until it is fresh again"),
            "Check the sensor and its link to the controller",
        ))
    return found


@dataclass(frozen=True)
class DeviceSeen:
    """A device as the worker last heard it (the device table), for the silence sweep."""

    device_id: str
    kind: str
    zone_id: str | None
    last_seen: datetime | None  # the newest ts of any message from it
    last_will_at: datetime | None  # its last will, until it sends anything new
    telemetry_ts: datetime | None  # a zone controller's newest ZONE_TELEMETRY


def silence_conditions(device: DeviceSeen, rules: AlertRules, now: datetime) -> list[Condition]:
    """DEVICE_OFFLINE for a device, and READING_STALE for a zone controller's readings (T1-34).
    A device never heard from gives nothing: it isn't connected yet, or isn't fitted."""
    if device.last_seen is None:
        return []
    zone = rules.zones.get(device.zone_id) if device.zone_id else None
    if device.kind == "ZONE_CONTROLLER":
        label = f"The {zone.name if zone else device.zone_id} controller ({device.device_id})"
    else:
        label = f"The {DEVICE_NAMES.get(device.kind, 'device')} ({device.device_id})"
    silent_s = (now - device.last_seen).total_seconds()
    offline = device.last_will_at is not None or silent_s >= rules.device_offline_s
    why = (
        "it dropped off the network (its last will)" if device.last_will_at is not None
        else f"it has sent nothing for {_duration(rules.device_offline_s)}"
    )
    zone_name = zone.name if zone else device.zone_id
    found = [Condition(
        "DEVICE_OFFLINE", f"DEVICE_OFFLINE:{device.device_id}", offline,
        "WARNING" if device.kind == "POWER_MONITOR" else "CRITICAL",
        f"{label} is offline: {why}",
        f"Doses to {zone_name} are refused, and its readings show as old, until it is back"
        if device.kind == "ZONE_CONTROLLER" else "Its readings show as old until it is back",
        "Check its power and network at the farm; the alert clears when it is heard again",
    )]
    if device.kind == "ZONE_CONTROLLER" and zone is not None and device.telemetry_ts is not None:
        old_s = (now - device.telemetry_ts).total_seconds()
        found.append(Condition(
            # Offline says more: while it is, this one stands down.
            "READING_STALE", f"READING_STALE:{device.zone_id}", not offline and old_s > rules.reading_stale_s,
            "WARNING",
            f"No new readings from {zone.name} for over {_duration(rules.reading_stale_s)}, "
            "though its controller is online",
            f"Doses to {zone.name} are refused until fresh readings arrive",
            "Check the zone's sensors and their link to the controller",
        ))
    return found


def _open(connection: Connection, org_id: str, site_id: str, key: str) -> Any:
    """The alert with this key that isn't cleared yet (raised or still waiting out its delay)."""
    return connection.execute(
        select(Alert.alert_id, Alert.raised_at, Alert.first_seen_at, Alert.severity).where(
            Alert.org_id == org_id, Alert.site_id == site_id, Alert.key == key, Alert.cleared_at.is_(None)
        )
    ).first()


def _queue_push(connection: Connection, org_id: str, site_id: str, alert_id: str, severity: str,
                now: datetime) -> None:
    """For the notifier (T1-41): this alert, at this severity, is to be pushed. Once only."""
    connection.execute(insert(PushOutbox).values(
        org_id=org_id, site_id=site_id, alert_id=alert_id, severity=severity, queued_at=now,
    ).on_conflict_do_nothing())


def _raise(
    connection: Connection, org_id: str, site_id: str, zone: str | None, condition: Condition,
    now: datetime, sim_ts: datetime | None, raised: bool,
) -> None:
    alert_id = str(uuid.uuid4())
    connection.execute(insert(Alert).values(
        org_id=org_id, site_id=site_id, alert_id=alert_id, key=condition.key,
        kind=condition.kind, severity=condition.severity, zone_id=zone,
        message=condition.message, system_action=condition.system_action, next_step=condition.next_step,
        first_seen_at=now, raised_at=now if raised else None, sim_ts=sim_ts,
    ))
    if raised:
        _queue_push(connection, org_id, site_id, alert_id, condition.severity, now)


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
    else:
        words = {  # the newest reading's, and its severity, so one raised as WARNING before turns red
            "severity": condition.severity,
            "message": condition.message,
            "system_action": condition.system_action,
            "next_step": condition.next_step,
        }
        if found.raised_at is None and now - found.first_seen_at >= delay:
            words["raised_at"] = now
        connection.execute(update(Alert).where(Alert.alert_id == found.alert_id).values(**words))
        turned_red = condition.severity == "CRITICAL" and found.severity != "CRITICAL"
        if "raised_at" in words or (found.raised_at is not None and turned_red):
            _queue_push(connection, org_id, site_id, found.alert_id, condition.severity, now)


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
