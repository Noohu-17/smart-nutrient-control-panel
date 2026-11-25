"""The routes of the read API: GET /api/farm, /api/zones, /api/zones/{zoneId}, /api/climate,
/api/power and /api/devices.

Plain functions rather than async ones: they read files and use the synchronous database engine,
so FastAPI runs them in its thread pool and the event loop stays free. Errors are ordinary
HTTPExceptions, which app.errors answers in the §11.1 shape.
"""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as NoConnectionFree

from ..config import Settings, load_settings
from ..db.engine import create_db_engine
from ..packs import NotImported, Pack, kept_pack, load_pack, refresh_soon
from . import queries
from .pack import (
    PackError,
    SiteRef,
    read_farm,
    read_sensors,
    read_zone_details,
    site_of,
    zone_sources,
)
from .schemas import (
    ClimateStatus,
    DeviceInfo,
    DeviceList,
    ErrorBody,
    Farm,
    PowerStatus,
    SensorCatalogue,
    ZoneCrop,
    ZoneList,
    ZoneProfile,
    ZoneStatus,
)
from .status import climate_status, is_online, power_status, zone_status

log = logging.getLogger("api")

MAX_PAGE = 200  # contract §11.1

ZONE_ID = re.compile(r"Z[0-9]+")  # §2.1

# The database can't answer right now: it's down, restarting, busy, or not migrated yet.
DATABASE_NOT_READY = (OperationalError, InterfaceError, ProgrammingError, NoConnectionFree)


@cache
def get_settings() -> Settings:
    return load_settings()


@cache
def _engine_for(settings: Settings) -> Engine:
    return create_db_engine(settings)  # connect_timeout and pool_timeout: a 503 soon, not a long wait


def get_engine(settings: Annotated[Settings, Depends(get_settings)]) -> Engine:
    return _engine_for(settings)


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]


def get_pack(settings: SettingsDep, engine: EngineDep) -> Pack:
    """The farm's settings (T1-39): the pack folder in dev, the newest imported version on a server.
    A new stored version also has its DEVICE_CONFIG sent to the zone controllers (§7.7)."""
    try:
        pack = load_pack(settings, engine)
    except NotImported as exc:
        log.error("%s", exc)
        raise HTTPException(503, "The farm settings have not been imported into the database yet") from exc
    from .device_config import send_soon  # here, as it needs this module's settings and engine

    send_soon(settings, engine, pack)
    return pack


PackDep = Annotated[Pack, Depends(get_pack)]


def get_stop_pack(settings: SettingsDep, engine: EngineDep) -> Pack:
    """Stop's pack (§8.3 rule 9): the version already in memory, without asking the database; a
    newer one is looked for in the background. Only when none has been loaded yet (the API has just
    started) is the database asked, and if it can't answer that is a 503, not a 500."""
    pack = kept_pack(settings, engine)
    if pack is not None:
        refresh_soon(settings, engine)
        return pack
    try:
        return get_pack(settings, engine)
    except PackError as exc:
        raise HTTPException(503, "The farm settings could not be loaded; try Stop again") from exc


StopPackDep = Annotated[Pack, Depends(get_stop_pack)]


def get_site(pack: PackDep) -> SiteRef:
    return site_of(pack)


SiteDep = Annotated[SiteRef, Depends(get_site)]


@contextmanager
def _database(engine: Engine) -> Iterator[Connection]:
    """A connection for one request. A database that can't answer is a 503, whatever the query."""
    try:
        with engine.connect() as connection:
            yield connection
    except DATABASE_NOT_READY as exc:
        log.warning("database not ready: %s", str(getattr(exc, "orig", exc)).partition("\n")[0])
        raise HTTPException(503, "The database is not available right now") from exc


def _not_loaded() -> HTTPException:
    """The seed hasn't run, which tells "not loaded yet" from "no such zone" or an empty farm."""
    return HTTPException(503, "The farm has not been loaded into the database yet")


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Tell OpenAPI these errors use the §11.1 body, which replaces FastAPI's own for 422."""
    return {status: {"model": ErrorBody} for status in statuses}


def get_farm(pack: PackDep) -> Farm:
    """The site's layout and feature switches (§11.2). From a pack folder, needs no database."""
    return read_farm(pack)


def get_sensors(pack: PackDep) -> SensorCatalogue:
    """The sensor catalogue per farm type (sensors.json, DM-3). 404 for a pack without one."""
    catalogue = read_sensors(pack)
    if catalogue is None:
        raise HTTPException(404, "This site's settings have no sensor catalogue")
    return catalogue


def list_zones(pack: PackDep, engine: EngineDep, site: SiteDep) -> ZoneList:
    """The latest readings and state of every zone, with what each zone is (§11.2, T1-38)."""
    details = _zone_details(pack)
    sources = zone_sources(pack)
    with _database(engine) as connection:
        rows = queries.zones(connection, site)
        if not rows and not queries.site_is_loaded(connection, site):
            raise _not_loaded()
        return ZoneList(
            items=[_described(_zone(connection, site, row, _source(sources, row)), details) for row in rows]
        )


def get_zone(
    zone_id: Annotated[str, Path(alias="zoneId")],
    pack: PackDep,
    engine: EngineDep,
    site: SiteDep,
) -> ZoneStatus:
    """A zone's latest readings and state. 200 even when offline; 404 means no such zone (§11.2)."""
    no_such_zone = HTTPException(404, f"There is no zone {zone_id}")
    if not ZONE_ID.fullmatch(zone_id):
        raise no_such_zone
    sources = zone_sources(pack)
    details = _zone_details(pack)
    with _database(engine) as connection:
        rows = queries.zones(connection, site, zone_id)
        if rows:
            return _described(_zone(connection, site, rows[0], _source(sources, rows[0])), details)
        if not queries.site_is_loaded(connection, site):
            raise _not_loaded()
        raise no_such_zone


def _source(sources: dict[str, str], row: Any) -> str:
    """The zone's source from the layout; a zone the layout no longer names is LIVE (safe)."""
    return sources.get(row.zone_id, "LIVE")


def _zone_details(pack: Pack) -> dict[str, tuple[ZoneProfile, ZoneCrop | None]]:
    """Each zone's profile and crop, or none at all if the pack can't give them: a mistake in
    recipes.json or io_map.json must not cost the farm its readings. The cause is logged."""
    try:
        return read_zone_details(pack)
    except PackError:
        log.warning("zones answered without profile and crop: the config pack could not be read")
        return {}


def _described(status: ZoneStatus, details: dict[str, tuple[ZoneProfile, ZoneCrop | None]]) -> ZoneStatus:
    """The zone answer with its profile and crop from the config pack (T1-38). A zone the database
    has but the layout no longer lists keeps them unset, so they are left out."""
    if status.zone not in details:
        return status
    profile, crop = details[status.zone]
    status.profile = profile  # assigning marks them set, so response_model_exclude_unset sends them
    status.crop = crop
    return status


def _zone(connection: Connection, site: SiteRef, row: Any, source: str) -> ZoneStatus:
    telemetry = (
        queries.latest_zone_telemetry(connection, site, row.zone_id, row.device_id)
        if row.device_id is not None
        else []
    )
    return zone_status(
        row.zone_id,
        source,
        row.dose_lines,
        row.last_seen,
        telemetry,
        datetime.now(UTC),
        last_will_at=row.last_will_at,
        state=row.state,
        leak=row.leak,
        active_cmd_id=row.active_cmd_id,
    )


def _seen(connection: Connection, site: SiteRef, device_id: str) -> Any:
    """The device's row, or None if the layout lists none by that ID: then it was never heard from.

    A site the seed hasn't loaded yet is a 503, as for the zones.
    """
    row = queries.device(connection, site, device_id)
    if row is None and not queries.site_is_loaded(connection, site):
        raise _not_loaded()
    return row


def get_climate(pack: PackDep, engine: EngineDep, site: SiteDep) -> ClimateStatus:
    """The climate controller's latest readings and state. 200 even when offline (§11.2)."""
    climate = read_farm(pack).climate
    with _database(engine) as connection:
        row = _seen(connection, site, climate.controller)
        rows = queries.latest_climate_telemetry(connection, site, climate.controller)
        state = queries.latest_message(connection, site, climate.controller, "CLIMATE_STATE")
    return climate_status(
        climate.air_sensors,
        row.last_seen if row is not None else None,
        rows,
        datetime.now(UTC),
        last_will_at=row.last_will_at if row is not None else None,
        state=state.body if state is not None else None,
    )


def get_power(pack: PackDep, engine: EngineDep, site: SiteDep) -> PowerStatus:
    """The power monitor's latest POWER_STATE. 200 even when offline (§11.2)."""
    power = read_farm(pack).power
    with _database(engine) as connection:
        row = _seen(connection, site, power.monitor)
        latest = queries.latest_message(connection, site, power.monitor, "POWER_STATE")
    return power_status(
        power.generator,
        power.ups,
        row.last_seen if row is not None else None,
        latest,
        datetime.now(UTC),
        last_will_at=row.last_will_at if row is not None else None,
    )


def list_devices(
    engine: EngineDep,
    site: SiteDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = MAX_PAGE,
    cursor: Annotated[str | None, Query(max_length=64, pattern=r"^[^\x00]+$")] = None,
) -> DeviceList:
    """Every device, when it was last seen and whether it is online (§11.1, §11.2)."""
    with _database(engine) as connection:
        rows = queries.devices(connection, site, limit + 1, cursor)
        if not rows and not queries.site_is_loaded(connection, site):
            raise _not_loaded()
    page, more = rows[:limit], len(rows) > limit
    now = datetime.now(UTC)
    return DeviceList(
        items=[
            DeviceInfo(
                device_id=row.device_id,
                kind=row.kind,
                zone=row.zone_id,
                online=is_online(row.last_seen, now, row.last_will_at),
                last_seen=row.last_seen,
            )
            for row in page
        ],
        next_cursor=page[-1].device_id if more else None,
    )


def add_read_api(app: FastAPI) -> None:
    """The routes go straight onto the app, so each one is listed in app.routes."""
    routes = (
        ("/api/farm", get_farm, Farm, (500,)),
        ("/api/sensors", get_sensors, SensorCatalogue, (404, 500)),
        ("/api/zones", list_zones, ZoneList, (503,)),
        ("/api/zones/{zoneId}", get_zone, ZoneStatus, (404, 422, 503)),
        ("/api/climate", get_climate, ClimateStatus, (500, 503)),
        ("/api/power", get_power, PowerStatus, (500, 503)),
    )
    for path, endpoint, model, errors in routes:
        app.add_api_route(
            path,
            endpoint,
            response_model=model,
            response_model_exclude_unset=True,
            responses=_errors(*errors),
            tags=["read"],
        )
    app.add_api_route(
        "/api/devices", list_devices, response_model=DeviceList, responses=_errors(422, 503), tags=["read"]
    )
