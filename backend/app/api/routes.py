"""The routes of the read API: GET /api/farm, /api/zones, /api/zones/{zoneId}, /api/devices.

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
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as NoConnectionFree

from ..config import Settings, load_settings
from ..db.engine import database_url
from . import queries
from .pack import SiteRef, read_farm, site_of
from .schemas import DeviceInfo, DeviceList, ErrorBody, Farm, ZoneList, ZoneStatus
from .status import is_online, zone_status

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
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,  # after a database restart, reconnect rather than fail one request
        pool_timeout=5,  # all connections busy: answer 503 soon rather than make the app wait
        connect_args={"connect_timeout": max(1, round(settings.health_timeout_s))},
    )


def get_engine(settings: Annotated[Settings, Depends(get_settings)]) -> Engine:
    return _engine_for(settings)


SettingsDep = Annotated[Settings, Depends(get_settings)]
EngineDep = Annotated[Engine, Depends(get_engine)]


def get_site(settings: SettingsDep) -> SiteRef:
    return site_of(settings.client_dir)


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


def get_farm(settings: SettingsDep) -> Farm:
    """The site's layout and feature switches (§11.2). Needs no database."""
    return read_farm(settings.client_dir)


def list_zones(engine: EngineDep, site: SiteDep) -> ZoneList:
    """The latest readings and state of every zone (§11.2)."""
    with _database(engine) as connection:
        rows = queries.zones(connection, site)
        if not rows and not queries.site_is_loaded(connection, site):
            raise _not_loaded()
        return ZoneList(items=[_zone(connection, site, row) for row in rows])


def get_zone(
    zone_id: Annotated[str, Path(alias="zoneId")], engine: EngineDep, site: SiteDep
) -> ZoneStatus:
    """A zone's latest readings and state. 200 even when offline; 404 means no such zone (§11.2)."""
    no_such_zone = HTTPException(404, f"There is no zone {zone_id}")
    if not ZONE_ID.fullmatch(zone_id):
        raise no_such_zone
    with _database(engine) as connection:
        rows = queries.zones(connection, site, zone_id)
        if rows:
            return _zone(connection, site, rows[0])
        if not queries.site_is_loaded(connection, site):
            raise _not_loaded()
        raise no_such_zone


def _zone(connection: Connection, site: SiteRef, row: Any) -> ZoneStatus:
    telemetry = (
        queries.latest_zone_telemetry(connection, site, row.zone_id, row.device_id)
        if row.device_id is not None
        else []
    )
    return zone_status(row.zone_id, row.dose_lines, row.last_seen, telemetry, datetime.now(UTC))


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
                online=is_online(row.last_seen, now),
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
        ("/api/zones", list_zones, ZoneList, (503,)),
        ("/api/zones/{zoneId}", get_zone, ZoneStatus, (404, 422, 503)),
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
