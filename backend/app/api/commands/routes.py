"""GET /api/commands/{cmdId}, POST /api/stop and POST /api/resume (contract §7.4, §7.8, §11.2).

Stop is never checked (§8.3 rule 9) and needs only the config pack and the broker, so it works
even while the database is down. Resume is sent only while its controller is online (§8.3 rule 9).
Roles (§13) wait for sign-in.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path
from sqlalchemy import select

from ...config import Settings
from ...db.models import CommandReport, Device
from ...errors import ApiError
from .. import queries
from ..pack import read_farm
from ..routes import EngineDep, SettingsDep, SiteDep, _database, _errors, _not_loaded
from ..status import is_online
from . import messages
from .messages import Command
from .publish import NotSent, Publisher, publisher_for
from .schemas import CommandSent, CommandStatus, ResumeBody, StopBody

log = logging.getLogger("api")


def get_publisher(settings: SettingsDep) -> Publisher:
    return publisher_for(settings)


PublisherDep = Annotated[Publisher, Depends(get_publisher)]


def _send(publisher: Publisher, command: Command) -> CommandSent:
    kind = command.body["kind"]
    try:
        publisher.publish(command.topic, command.body)
    except NotSent as exc:
        log.warning("%s %s not sent on %s: %s", kind, command.cmd_id, command.topic, exc)
        raise HTTPException(503, "The command could not be sent: the broker is not available") from exc
    log.info("sent %s %s on %s", kind, command.cmd_id, command.topic)
    return CommandSent(cmd_id=command.cmd_id, state="DISPATCHED")


def _no_such_zone(settings: Settings, zone_id: str) -> bool:
    return all(zone.id != zone_id for zone in read_farm(settings.client_dir).zones)


def get_command(
    cmd_id: Annotated[str, Path(alias="cmdId")], engine: EngineDep, site: SiteDep
) -> CommandStatus:
    """A command as its controller last reported it. 404 until the first CMD_STATE arrives."""
    not_found = HTTPException(404, f"There is no command {cmd_id}")
    try:
        key = str(uuid.UUID(cmd_id))  # stored as the model wrote it: lowercase, with hyphens
    except ValueError:
        raise not_found from None
    with _database(engine) as connection:
        body = connection.scalar(
            select(CommandReport.body).where(
                CommandReport.org_id == site.org_id,
                CommandReport.site_id == site.site_id,
                CommandReport.cmd_id == key,
            )
        )
    if body is None:
        raise not_found
    return CommandStatus.model_validate(body)


def post_stop(body: StopBody, settings: SettingsDep, site: SiteDep, publisher: PublisherDep) -> CommandSent:
    """Stop (§7.4): always allowed, for every role, with no check and no confirmation."""
    if body.zone is not None and _no_such_zone(settings, body.zone):
        raise HTTPException(404, f"There is no zone {body.zone}")
    command = messages.stop(site.site_id, body.scope, body.zone, datetime.now(UTC))
    return _send(publisher, command)


def post_resume(
    body: ResumeBody, settings: SettingsDep, engine: EngineDep, site: SiteDep, publisher: PublisherDep
) -> CommandSent:
    """Resume (§7.8) a zone or climate after a stop, once its controller is online (§8.3 rule 9)."""
    farm = read_farm(settings.client_dir)
    now = datetime.now(UTC)
    if body.zone is not None:
        zone = next((z for z in farm.zones if z.id == body.zone), None)
        if zone is None:
            raise HTTPException(404, f"There is no zone {body.zone}")
        controller, command = zone.controller, messages.resume_zone(site.site_id, zone.id, now)
    else:
        controller, command = farm.climate.controller, messages.resume_climate(site.site_id, now)
    with _database(engine) as connection:
        device = connection.execute(
            select(Device.last_seen, Device.last_will_at).where(
                Device.org_id == site.org_id, Device.site_id == site.site_id, Device.device_id == controller
            )
        ).first()
        if device is None and not queries.site_is_loaded(connection, site):
            raise _not_loaded()
    if device is None or not is_online(device.last_seen, now, device.last_will_at):
        raise ApiError(409, "DEVICE_OFFLINE", f"{controller} is offline, so nothing was sent")
    return _send(publisher, command)


def add_command_api(app: FastAPI) -> None:
    app.add_api_route(
        "/api/commands/{cmdId}",
        get_command,
        methods=["GET"],
        response_model=CommandStatus,
        responses=_errors(404, 422, 503),  # 422 too, so OpenAPI shows the §11.1 body, not FastAPI's
        tags=["commands"],
    )
    for path, endpoint, errors in (
        ("/api/stop", post_stop, (404, 422, 503)),
        ("/api/resume", post_resume, (404, 409, 422, 503)),
    ):
        app.add_api_route(
            path,
            endpoint,
            methods=["POST"],
            status_code=202,
            response_model=CommandSent,
            responses=_errors(*errors),
            tags=["commands"],
        )
