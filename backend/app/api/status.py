"""The rules behind a zone's, the climate's, the power's and a device's status (§11.2, §14)."""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, get_origin

from ..contract import ContractModel
from ..contract.farm_messages import ClimateReadings, Phases, ZoneReadings
from .schemas import ClimateStatus, PowerStatus, ZoneStatus

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


# The names POWER_STATE's numbers go by in a quality map: the phases as in §6.3, the UPS values
# from the units table (§1).
UPS_VALUES = ("upsBatteryPct", "upsRuntimeMin")


def _no_readings(model: type[ContractModel], keys: Iterable[str]) -> dict[str, Any]:
    """Every reading as null: the map readings per key (dose line, sensor position), the others on their own."""
    per_key = dict.fromkeys(keys)
    return {
        info.alias: dict(per_key) if get_origin(info.annotation) is dict else None
        for info in model.model_fields.values()
    }


def _readings_and_quality(
    rows: Sequence[Any], readings: dict[str, Any], stale: bool
) -> dict[str, str]:
    """Put one message's reading rows into `readings`; returns their quality.

    Readings stay as sent. A quality of OK turns into STALE when `stale`; every other stays.
    """
    quality: dict[str, str] = {}
    for row in rows:
        name, _, key = row.metric.partition(".")
        if key:
            readings.setdefault(name, {})[key] = row.value
        else:
            readings[name] = row.value
        if row.quality is not None:
            quality[name] = "STALE" if stale and row.quality == "OK" else row.quality
    return quality


def _sim_ts(latest: Any) -> dict[str, Any]:
    """simTs only when the latest message has it (playground sites), so it's left out otherwise."""
    return {"sim_ts": latest.sim_ts} if latest is not None and latest.sim_ts is not None else {}


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
    readings = _no_readings(ZoneReadings, dose_lines)
    quality = _readings_and_quality(rows, readings, stale)
    return ZoneStatus(
        zone=zone_id,
        online=online,
        last_seen=last_seen,
        ts=latest.ts if latest is not None else None,
        **_sim_ts(latest),
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


def climate_status(
    air_sensors: Sequence[str],
    last_seen: datetime | None,
    rows: Sequence[Any],
    now: datetime,
    *,
    last_will_at: datetime | None = None,
    state: Mapping[str, Any] | None = None,
) -> ClimateStatus:
    """The climate answer (§11.2), from its latest CLIMATE_TELEMETRY rows and its latest CLIMATE_STATE.

    Readings and quality follow the zone answer's rules. mode, manualUntil, stopped, equipment and
    latches come from the latest CLIMATE_STATE, and are null, {} or [] while none has been received.
    """
    online = is_online(last_seen, now, last_will_at)
    known = state or {}
    latest = rows[0] if rows else None
    stale = not online or (latest is not None and now - latest.ts > FRESH_FOR)
    readings = _no_readings(ClimateReadings, air_sensors)
    quality = _readings_and_quality(rows, readings, stale)
    return ClimateStatus(
        online=online,
        last_seen=last_seen,
        ts=latest.ts if latest is not None else None,
        **_sim_ts(latest),
        readings=ClimateReadings.model_validate(readings),
        quality=quality,
        mode=known.get("mode"),
        manual_until=known.get("manualUntil"),
        stopped=known.get("stopped"),
        equipment=known.get("equipment", {}),
        latches=known.get("latches", []),
    )


def power_status(
    has_generator: bool,
    has_ups: bool,
    last_seen: datetime | None,
    latest: Any,
    now: datetime,
    *,
    last_will_at: datetime | None = None,
) -> PowerStatus:
    """The power answer (§11.2), from the latest POWER_STATE (`latest`: ts, sim_ts and body), or None.

    POWER_STATE sends no quality, so quality is {} unless the message is older than the fresh
    limit or the monitor is offline: then every value is STALE. generator and ups are left out
    when the layout has none, and null when it has them but nothing has been received.
    """
    online = is_online(last_seen, now, last_will_at)
    body = latest.body if latest is not None else {}
    names = [info.alias for info in Phases.model_fields.values()] + (list(UPS_VALUES) if has_ups else [])
    stale = latest is not None and (not online or now - latest.ts > FRESH_FOR)
    optional = {}
    if has_generator:
        optional["generator"] = body.get("generator")
    if has_ups:
        optional["ups"] = body.get("ups")
    return PowerStatus(
        online=online,
        last_seen=last_seen,
        ts=latest.ts if latest is not None else None,
        **_sim_ts(latest),
        mains=body.get("mains"),
        phases=Phases.model_validate(body.get("phases") or _no_readings(Phases, ())),
        source=body.get("source"),
        **optional,
        ats=body.get("ats"),
        quality=dict.fromkeys(names, "STALE") if stale else {},
    )
