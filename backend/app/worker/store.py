"""What the worker writes: readings, each device's latest state, and when each was last seen."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, Engine, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert

from ..contract import ClimateTelemetry, DeviceStatus, Envelope, PowerState, ZoneTelemetry
from ..db.models import Device, LatestMessage, Reading

# The kinds of message whose newest one per device is kept (models.LatestMessage).
KEPT_TYPES = frozenset({"ZONE_STATE", "CLIMATE_STATE", "ZONE_TELEMETRY", "POWER_STATE"})


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


def _insert_rows(connection: Connection, rows: list[dict[str, Any]]) -> int:
    """Insert one message's rows. Returns how many weren't already stored.

    A repeated message gives the same primary keys, so ON CONFLICT DO NOTHING drops it
    (models.Reading).
    """
    statement = insert(Reading).values(rows).on_conflict_do_nothing()
    inserted = connection.execute(statement.returning(Reading.metric))  # new rows only
    return len(inserted.all())


def store_rows(engine: Engine, rows: list[dict[str, Any]]) -> int:
    """Store one message's rows in one transaction. Returns how many weren't already stored."""
    with engine.begin() as connection:
        return _insert_rows(connection, rows)


def _keep_if_newer(connection: Connection, message: Envelope, org_id: str) -> bool:
    """Make `message` its device's latest of its kind, unless a newer one is already kept.

    (bootId, seq) orders a device's messages (§5): bootId rises at every restart, and seq at every
    message within a boot. Returns whether the message was kept.
    """
    row = {
        "org_id": org_id,
        "site_id": message.site,
        "device_id": message.device,
        "type": message.type,
        "zone_id": getattr(message, "zone", None),
        "boot_id": message.boot_id,
        "seq": message.seq,
        "ts": message.ts,
        "sim_ts": message.sim_ts,
        "body": message.model_dump(mode="json"),
    }
    keys = [column.name for column in LatestMessage.__table__.primary_key.columns]
    statement = insert(LatestMessage).values(row)
    newer = tuple_(statement.excluded.boot_id, statement.excluded.seq) > tuple_(
        LatestMessage.boot_id, LatestMessage.seq
    )
    statement = statement.on_conflict_do_update(
        index_elements=keys,
        set_={name: statement.excluded[name] for name in row if name not in keys},
        where=newer,
    )
    return connection.execute(statement.returning(LatestMessage.seq)).first() is not None


def _the_device(org_id: str, site_id: str, device_id: str) -> tuple[ColumnElement[bool], ...]:
    return Device.org_id == org_id, Device.site_id == site_id, Device.device_id == device_id


@dataclass(frozen=True)
class Stored:
    rows: int  # how many readings the message had
    new_rows: int  # how many of them weren't already stored
    kept: bool | None  # whether it became the latest of its kind; None if its kind isn't kept

    @property
    def replay(self) -> bool:
        """Nothing new: its readings were all stored already, or a newer message of its kind was."""
        return self.new_rows == 0 if self.rows else self.kept is False


def store_message(
    engine: Engine,
    message: Envelope,
    org_id: str,
    rows: list[dict[str, Any]],
    received_at: datetime,
) -> Stored:
    """Everything one checked message changes, in one transaction.

    Its readings, the latest message of its kind (KEPT_TYPES), and its device's last_seen, which
    never moves back. A DEVICE_STATUS saying offline counts as a last will. Anything else new from
    the device clears its last will; a replay doesn't.
    """
    with engine.begin() as connection:
        new_rows = _insert_rows(connection, rows) if rows else 0
        kept = _keep_if_newer(connection, message, org_id) if message.type in KEPT_TYPES else None
        stored = Stored(len(rows), new_rows, kept)
        seen: dict[str, Any] = {"last_seen": func.greatest(Device.last_seen, message.ts)}
        if isinstance(message, DeviceStatus) and not message.online:
            seen["last_will_at"] = received_at
        elif not stored.replay:
            # The device is back. Its ts isn't compared with when the last will arrived: they
            # come from different clocks (the device's and the server's).
            seen["last_will_at"] = None
        connection.execute(
            update(Device).where(*_the_device(org_id, message.site, message.device)).values(seen)
        )
    return stored


def mark_offline(
    engine: Engine, org_id: str, site_id: str, device_id: str, received_at: datetime
) -> bool:
    """A last will (§6.4): the device is offline from now until it sends anything new (§14).

    A last will has no ts, so the time it arrived stands in. Returns whether the device is known.
    """
    with engine.begin() as connection:
        changed = connection.execute(
            update(Device)
            .where(*_the_device(org_id, site_id, device_id))
            .values(last_will_at=received_at)
        )
        return changed.rowcount > 0


def last_seen_times(engine: Engine, org_id: str, site_id: str) -> dict[str, datetime]:
    """When each device was last heard from, by its readings: the newest ts among them.

    It's the message's ts, not the time of arrival, so a retained or replayed message can't
    make a silent device look alive, and an old one can't move this back. device.last_seen is
    the same, from every message, readings or not.
    """
    statement = (
        select(Reading.device_id, func.max(Reading.ts))
        .where(Reading.org_id == org_id, Reading.site_id == site_id)
        .group_by(Reading.device_id)
    )
    with engine.connect() as connection:
        return {device: ts for device, ts in connection.execute(statement)}
