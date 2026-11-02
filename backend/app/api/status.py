"""The rules behind a zone's and a device's status (contract §11.2, §14)."""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any, get_origin

from ..contract.farm_messages import ZoneReadings
from .schemas import ZoneStatus

OFFLINE_AFTER = timedelta(minutes=3)  # "3 minutes without any message" (§14)
FRESH_FOR = timedelta(seconds=120)  # "Fresh EC/pH to dose: ≤ 120 s old" (§14)


def is_online(last_seen: datetime | None, now: datetime) -> bool:
    return last_seen is not None and now - last_seen < OFFLINE_AFTER


def _no_readings(dose_lines: Iterable[str]) -> dict[str, Any]:
    """Every reading as null: the map readings per dose line, the others on their own."""
    per_line = dict.fromkeys(dose_lines)
    return {
        info.alias: dict(per_line) if get_origin(info.annotation) is dict else None
        for info in ZoneReadings.model_fields.values()
    }


def zone_status(
    zone_id: str,
    dose_lines: Sequence[str],
    last_seen: datetime | None,
    rows: Sequence[Any],
    now: datetime,
) -> ZoneStatus:
    """A zone's answer, from the rows of its latest telemetry (none if it has never reported).

    Readings stay as sent. A quality of OK turns into STALE when the reading is older than the
    fresh limit or its controller is offline; every other quality stays as it is (§11.2).

    The fields that come from ZONE_STATE and the rest of ZONE_TELEMETRY aren't in the reading
    table, so they say "not known", in the form §11.2 gives a zone that has never reported.
    Unknown means blocked (§8.3), so a start command can't go ahead on the strength of them.
    """
    online = is_online(last_seen, now)
    latest = rows[0] if rows else None
    stale = not online or (latest is not None and now - latest.ts > FRESH_FOR)
    readings = _no_readings(dose_lines)
    quality: dict[str, str] = {}
    for row in rows:
        name, _, key = row.metric.partition(".")
        if key:
            readings.setdefault(name, {})[key] = row.value
        else:
            readings[name] = row.value
        if row.quality is not None:
            quality[name] = "STALE" if stale and row.quality == "OK" else row.quality
    sim_ts = {"sim_ts": latest.sim_ts} if latest is not None and latest.sim_ts is not None else {}
    return ZoneStatus(
        zone=zone_id,
        online=online,
        last_seen=last_seen,
        ts=latest.ts if latest is not None else None,
        **sim_ts,
        readings=ZoneReadings.model_validate(readings),
        quality=quality,
        equipment={},
        mode=None,
        estop=None,
        leak=None,
        stopped=None,
        active_cmd_id=None,
        operation=None,
        latches=[],
        config_version=None,
    )
