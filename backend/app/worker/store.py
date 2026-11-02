"""What the worker writes to the reading table, and when each device was last seen."""

from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert

from ..contract import ClimateTelemetry, DeviceStatus, Envelope, PowerState, ZoneTelemetry
from ..db.models import Reading


def _power_values(state: PowerState) -> dict[str, float | None]:
    """A POWER_STATE's numbers, named as in the units table (contract §1: l1V, upsBatteryPct)."""
    values = state.phases.model_dump()
    if state.ups:
        values |= {"upsBatteryPct": state.ups.battery_pct, "upsRuntimeMin": state.ups.runtime_min}
    return values


def reading_rows(message: Envelope, org_id: str) -> list[dict[str, Any]]:
    """One reading-table row per value in `message` (models.Reading), or none if it has no values.

    Values and qualities stay as sent: a null is stored as null, and a reading with no
    quality in the message has none here. An entry of a map reading (dayTankPct …) takes the
    quality given for the whole map (§6.1).

    A heartbeat gives one row, its uptimeS, so every device has a new row at least every 30 s
    (§6.4). last_seen_times() relies on that.
    """
    match message:
        case ZoneTelemetry() | ClimateTelemetry():
            values, quality = message.readings.model_dump(), message.quality
        case PowerState():
            values, quality = _power_values(message), {}
        case DeviceStatus():
            values, quality = {"uptimeS": message.uptime_s}, {}
        case _:
            return []
    common = {
        "org_id": org_id,
        "site_id": message.site,
        "device_id": message.device,
        "ts": message.ts,
        "sim_ts": message.sim_ts,
        "zone_id": message.zone if isinstance(message, ZoneTelemetry) else None,
    }
    rows = []
    for name, value in values.items():
        entries = value.items() if isinstance(value, dict) else [(None, value)]
        for key, entry in entries:
            metric = name if key is None else f"{name}.{key}"
            rows.append(common | {"metric": metric, "value": entry, "quality": quality.get(name)})
    return rows


def store_rows(engine: Engine, rows: list[dict[str, Any]]) -> int:
    """Store one message's rows in one transaction. Returns how many weren't already stored.

    A repeated message gives the same primary keys, so ON CONFLICT DO NOTHING drops it
    (models.Reading).
    """
    statement = insert(Reading).values(rows).on_conflict_do_nothing()
    with engine.begin() as connection:
        inserted = connection.execute(statement.returning(Reading.metric))  # new rows only
        return len(inserted.all())


def last_seen_times(engine: Engine, org_id: str, site_id: str) -> dict[str, datetime]:
    """When each device was last heard from: the newest ts among its readings.

    It's the message's ts, not the time of arrival, so a retained or replayed message can't
    make a silent device look alive, and an old one can't move this back.
    """
    statement = (
        select(Reading.device_id, func.max(Reading.ts))
        .where(Reading.org_id == org_id, Reading.site_id == site_id)
        .group_by(Reading.device_id)
    )
    with engine.connect() as connection:
        return {device: ts for device, ts in connection.execute(statement)}
