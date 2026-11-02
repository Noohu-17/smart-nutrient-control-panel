"""The read API's database queries. Every one is scoped by org_id and site_id (OPEN-11).

These run on every screen refresh against a table that keeps growing, so each finds the newest
row through the reading table's primary key (org, site, device, metric, ts) rather than by
scanning: the cost follows the number of TimescaleDB chunks, not the number of readings.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, Connection, and_, exists, func, select

from ..db.models import Device, Reading, Site, Zone
from .pack import SiteRef

# One metric that every kind of stored message always has, so the newest ts among these for a
# device is the newest ts of all its readings: uptimeS (DEVICE_STATUS), ec (ZONE_TELEMETRY),
# airTempC (CLIMATE_TELEMETRY) and l1V (POWER_STATE). See app.worker.store.reading_rows.
EVERY_KIND_OF_MESSAGE = ("uptimeS", "ec", "airTempC", "l1V")
ZONE_TELEMETRY_METRIC = "ec"
ZONE_CONTROLLER = "ZONE_CONTROLLER"


def _in_site(model: Any, site: SiteRef) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return model.org_id == site.org_id, model.site_id == site.site_id


def _last_seen(site: SiteRef) -> ColumnElement[Any]:
    """The newest ts of any reading from the Device in the enclosing query (§11.2)."""
    newest = [
        select(Reading.ts)
        .where(
            *_in_site(Reading, site),
            Reading.device_id == Device.device_id,
            Reading.metric == metric,
        )
        .order_by(Reading.ts.desc())
        .limit(1)
        .correlate(Device)
        .scalar_subquery()
        for metric in EVERY_KIND_OF_MESSAGE
    ]
    return func.greatest(*newest)  # ignores nulls, and is null only if all of them are


def site_is_loaded(connection: Connection, site: SiteRef) -> bool:
    """Whether the seed has run for this site, which tells "no such zone" from "not loaded yet"."""
    return bool(connection.scalar(select(exists().where(*_in_site(Site, site)))))


def zones(connection: Connection, site: SiteRef, zone_id: str | None = None) -> Sequence[Any]:
    """Zones in layout order (Z1, Z2, Z10), each with its controller and when it was last seen."""
    controller_of_zone = and_(
        Device.org_id == Zone.org_id,
        Device.site_id == Zone.site_id,
        Device.zone_id == Zone.zone_id,
        Device.kind == ZONE_CONTROLLER,
    )
    statement = (
        select(Zone.zone_id, Zone.dose_lines, Device.device_id, _last_seen(site).label("last_seen"))
        .select_from(Zone)
        .outerjoin(Device, controller_of_zone)
        .where(*_in_site(Zone, site))
        .order_by(func.length(Zone.zone_id), Zone.zone_id)
    )
    if zone_id is not None:
        statement = statement.where(Zone.zone_id == zone_id)
    return connection.execute(statement).all()


def latest_zone_telemetry(
    connection: Connection, site: SiteRef, zone_id: str, controller: str
) -> Sequence[Any]:
    """Every value of the zone's newest ZONE_TELEMETRY: one message's rows share its ts."""
    theirs = (*_in_site(Reading, site), Reading.device_id == controller, Reading.zone_id == zone_id)
    newest = (
        select(Reading.ts)
        .where(*theirs, Reading.metric == ZONE_TELEMETRY_METRIC)
        .order_by(Reading.ts.desc())
        .limit(1)
        .scalar_subquery()
    )
    return connection.execute(
        select(Reading.metric, Reading.value, Reading.quality, Reading.ts, Reading.sim_ts).where(
            *theirs, Reading.ts == newest
        )
    ).all()


def devices(
    connection: Connection, site: SiteRef, limit: int, after: str | None = None
) -> Sequence[Any]:
    """Up to `limit` devices by ID, after the device `after`, with when each was last seen."""
    statement = (
        select(Device.device_id, Device.kind, Device.zone_id, _last_seen(site).label("last_seen"))
        .where(*_in_site(Device, site))
        .order_by(Device.device_id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(Device.device_id > after)
    return connection.execute(statement).all()
