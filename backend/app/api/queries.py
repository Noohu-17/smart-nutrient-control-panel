"""The read API's database queries. Every one is scoped by org_id and site_id (OPEN-11).

When a device was last seen, and its latest state, are kept up to date by the worker
(device.last_seen, device.last_will_at and latest_message), so reading them is one row each.
A zone's latest readings are found through the reading table's primary key (org, site,
device, metric, ts) rather than by scanning: the cost follows the number of TimescaleDB
chunks, not the number of readings.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, and_, exists, func, select
from sqlalchemy.orm import aliased

from ..db.models import CommandReport, Device, LatestMessage, PlaygroundReset, Reading, Site, Zone
from .pack import SiteRef

ZONE_TELEMETRY_METRIC = "ec"
CLIMATE_TELEMETRY_METRIC = "airTempC"  # one of every CLIMATE_TELEMETRY's rows, null or not
ZONE_CONTROLLER = "ZONE_CONTROLLER"


def _in_site(model: Any, site: SiteRef) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return model.org_id == site.org_id, model.site_id == site.site_id


def site_is_loaded(connection: Connection, site: SiteRef) -> bool:
    """Whether the seed has run for this site, which tells "no such zone" from "not loaded yet"."""
    return bool(connection.scalar(select(exists().where(*_in_site(Site, site)))))


def zones(connection: Connection, site: SiteRef, zone_id: str | None = None) -> Sequence[Any]:
    """Zones in layout order (Z1, Z2, Z10), each with its controller and that controller's state.

    The state comes from the controller's latest ZONE_STATE for this zone, and leak and
    activeCmdId from its latest ZONE_TELEMETRY for this zone (§11.2). A controller's messages
    for another zone, from before the layout moved it, don't count.
    """
    controller_of_zone = and_(
        Device.org_id == Zone.org_id,
        Device.site_id == Zone.site_id,
        Device.zone_id == Zone.zone_id,
        Device.kind == ZONE_CONTROLLER,
    )
    state = aliased(LatestMessage, name="zone_state")
    telemetry = aliased(LatestMessage, name="zone_telemetry")

    def latest(message: Any, kind: str) -> ColumnElement[bool]:
        return and_(
            message.org_id == Device.org_id,
            message.site_id == Device.site_id,
            message.device_id == Device.device_id,
            message.type == kind,
            message.zone_id == Zone.zone_id,
        )

    statement = (
        select(
            Zone.zone_id,
            Zone.dose_lines,
            Device.device_id,
            Device.last_seen,
            Device.last_will_at,
            state.body.label("state"),
            telemetry.body["leak"].label("leak"),  # JSON values: true/false, a UUID or null
            telemetry.body["activeCmdId"].label("active_cmd_id"),
        )
        .select_from(Zone)
        .outerjoin(Device, controller_of_zone)
        .outerjoin(state, latest(state, "ZONE_STATE"))
        .outerjoin(telemetry, latest(telemetry, "ZONE_TELEMETRY"))
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


def device(connection: Connection, site: SiteRef, device_id: str) -> Any:
    """A device with when it was last seen and its last will, or None if the layout hasn't got it."""
    return connection.execute(
        select(Device.device_id, Device.last_seen, Device.last_will_at).where(
            *_in_site(Device, site), Device.device_id == device_id
        )
    ).first()


def latest_message(connection: Connection, site: SiteRef, device_id: str, kind: str) -> Any:
    """The device's latest message of this kind (ts, sim_ts and body), or None if none was kept."""
    return connection.execute(
        select(LatestMessage.ts, LatestMessage.sim_ts, LatestMessage.body).where(
            *_in_site(LatestMessage, site),
            LatestMessage.device_id == device_id,
            LatestMessage.type == kind,
        )
    ).first()


def latest_climate_telemetry(connection: Connection, site: SiteRef, controller: str) -> Sequence[Any]:
    """Every value of the controller's newest CLIMATE_TELEMETRY, found as the zone's are."""
    theirs = (*_in_site(Reading, site), Reading.device_id == controller)
    newest = (
        select(Reading.ts)
        .where(*theirs, Reading.metric == CLIMATE_TELEMETRY_METRIC)
        .order_by(Reading.ts.desc())
        .limit(1)
        .scalar_subquery()
    )
    return connection.execute(
        select(Reading.metric, Reading.value, Reading.quality, Reading.ts, Reading.sim_ts).where(
            *theirs, Reading.ts == newest
        )
    ).all()


def delivered_ml_since(
    connection: Connection, site: SiteRef, controller: str, since: datetime, *, simulated: bool
) -> dict[str, float]:
    """Delivered mL per line in the controller's commands reported after `since` (§9 rule 8).

    Each command counts once, by its newest report, at that report's simTs on playground sites
    (its ts if it has none) and its ts elsewhere (§11.4). On a playground site a reset clears the
    totals (§11.4), so only reports sent after the last reset count.
    """
    at = func.coalesce(CommandReport.sim_ts, CommandReport.ts) if simulated else CommandReport.ts
    which = [*_in_site(CommandReport, site), CommandReport.device_id == controller, at > since]
    if simulated and (reset_at := playground_reset_at(connection, site)) is not None:
        which.append(CommandReport.ts > reset_at)
    reports = connection.scalars(select(CommandReport.body).where(*which))
    totals: dict[str, float] = {}
    for body in reports:
        for line in body.get("lines") or []:
            totals[line["line"]] = totals.get(line["line"], 0) + line["deliveredMl"]
    return totals


def playground_reset_at(connection: Connection, site: SiteRef) -> datetime | None:
    """When the playground's farm was last reset (real time), or None if it never was."""
    return connection.scalar(select(PlaygroundReset.reset_at).where(*_in_site(PlaygroundReset, site)))


def devices(
    connection: Connection, site: SiteRef, limit: int, after: str | None = None
) -> Sequence[Any]:
    """Up to `limit` devices by ID, after the device `after`, with when each was last seen."""
    statement = (
        select(Device.device_id, Device.kind, Device.zone_id, Device.last_seen, Device.last_will_at)
        .where(*_in_site(Device, site))
        .order_by(Device.device_id)
        .limit(limit)
    )
    if after is not None:
        statement = statement.where(Device.device_id > after)
    return connection.execute(statement).all()
