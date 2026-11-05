"""The rules behind a zone's and a device's status (contract §11.2, §14)."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, get_origin

from ..contract.farm_messages import ZoneReadings
from .schemas import ZoneStatus

OFFLINE_AFTER = timedelta(minutes=3)  # "3 minutes without any message" (§14)
FRESH_FOR = timedelta(seconds=120)  # "Fresh EC/pH to dose: ≤ 120 s old" (§14)


def is_online(
    last_seen: datetime | None, now: datetime, last_will_at: datetime | None = None
) -> bool:
    """Heard from within 3 minutes, and no last will waiting (§14).

    The worker clears a device's last will when the device sends anything new, so last_seen (the
    device's clock) and last_will_at (the server's) are never compared.
    """
    if last_will_at is not None or last_seen is None:
        return False
    return now - last_seen < OFFLINE_AFTER


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
    *,
    last_will_at: datetime | None = None,
    state: Mapping[str, Any] | None = None,
    leak: bool | None = None,
    active_cmd_id: str | None = None,
) -> ZoneStatus:
    """A zone's answer (§11.2), from the rows of its latest telemetry and its latest ZONE_STATE.

    Readings stay as sent. A quality of OK turns into STALE when the reading is older than the
    fresh limit or its controller is offline; every other quality stays as it is.

    mode, estop, stopped, operation, latches, configVersion and equipment (ZONE_STATE's outputs)
    come from the latest ZONE_STATE; leak and activeCmdId from the latest ZONE_TELEMETRY. They
    are null, {} or [] only while none has been received. Unknown means blocked (§8.3).
    """
    online = is_online(last_seen, now, last_will_at)
    known = state or {}
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
        equipment=known.get("outputs", {}),  # §6.8 calls them outputs, §11.2 equipment
        mode=known.get("mode"),
        estop=known.get("estop"),
        leak=leak,
        stopped=known.get("stopped"),
        active_cmd_id=active_cmd_id,
        operation=known.get("operation"),
        latches=known.get("latches", []),
        config_version=known.get("configVersion"),
    )
