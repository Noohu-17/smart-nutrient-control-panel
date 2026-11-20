"""GET /api/commands/{cmdId}, POST /api/stop, POST /api/resume and
POST /api/dose-requests/{id}/confirm (contract §7.2, §7.4, §7.8, §11.2, §11.3).

Stop is never checked (§8.3 rule 9) and needs only the config pack, the broker and a signed-in
session, which it checks without the database, so it works even while the database is down.
Every role may Stop; Resume and Confirm are for Owner, Agronomist and Operator (§13). Resume is
sent only while its controller is online (§8.3 rule 9). Confirm checks the dose again and sends
it once (confirm.py). Each command names who sent it in issuedBy (§7.1) and the audit log.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from sqlalchemy import select

from ...auth.access import STAFF, Caller, StopCaller, allowed
from ...db.models import CommandReport, Device
from ...errors import ApiError
from .. import queries
from ..dose_requests import MayDose
from ..pack import Pack, read_farm, site_of
from ..routes import (
    EngineDep,
    PackDep,
    SettingsDep,
    SiteDep,
    StopPackDep,
    _database,
    _errors,
    _not_loaded,
)
from ..status import is_online
from . import messages, sent, stuck
from .confirm import confirm
from .messages import Command
from .publish import NotSent, Publisher, publisher_for
from .schemas import CommandSent, CommandStatus, ResumeBody, StopBody

log = logging.getLogger("api")


def get_publisher(settings: SettingsDep) -> Publisher:
    return publisher_for(settings)


PublisherDep = Annotated[Publisher, Depends(get_publisher)]

may_operate = allowed(*STAFF, why="Viewers can’t resume")  # §11.2: Owner, Agronomist, Operator
MayOperate = Annotated[Caller, Depends(may_operate)]


def _send(publisher: Publisher, command: Command) -> CommandSent:
    kind = command.body["kind"]
    try:
        publisher.publish(command.topic, command.body)
    except NotSent as exc:
        log.warning("%s %s not sent on %s: %s", kind, command.cmd_id, command.topic, exc)
        raise HTTPException(503, "The command could not be sent: the broker is not available") from exc
    log.info("sent %s %s on %s", kind, command.cmd_id, command.topic)
    return CommandSent(cmd_id=command.cmd_id, state="DISPATCHED")


def _no_such_zone(pack: Pack, zone_id: str) -> bool:
    return all(zone.id != zone_id for zone in read_farm(pack).zones)


def get_command(
    cmd_id: Annotated[str, Path(alias="cmdId")], engine: EngineDep, site: SiteDep
) -> CommandStatus:
    """A command as its controller last reported it, or DISPATCHED from when the broker has it
    until the first CMD_STATE arrives. 404 for a command nobody has sent or reported.
    UNKNOWN, TIMED_OUT once the farm has left it without replies (stuck.py)."""
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
        marked = stuck.timed_out(connection, site, key)
        if body is None and sent.dispatched(connection, site, key):
            return stuck.shown(CommandStatus.dispatched(key), marked)
    if body is None:
        raise not_found
    return stuck.shown(CommandStatus.model_validate(body), marked)


def post_stop(
    body: StopBody, pack: StopPackDep, engine: EngineDep, publisher: PublisherDep, session: StopCaller,
) -> CommandSent:
    """Stop (§7.4): always allowed, for every signed-in role, Viewer included (§13), with no check
    and no confirmation. The session is checked without the database (app.auth.access.stop_caller),
    and the farm's settings are the version kept in memory (app.api.routes.get_stop_pack)."""
    site = site_of(pack)
    if body.zone is not None and _no_such_zone(pack, body.zone):
        raise HTTPException(404, f"There is no zone {body.zone}")
    command = messages.stop(site.site_id, body.scope, body.zone, datetime.now(UTC), f"user:{session.user_id}")
    answer = _send(publisher, command)
    sent.record(engine, site, command, datetime.now(UTC), user_id=session.user_id)
    return answer


def post_resume(
    body: ResumeBody, pack: PackDep, engine: EngineDep, site: SiteDep, publisher: PublisherDep,
    caller: MayOperate,
) -> CommandSent:
    """Resume (§7.8) a zone or climate after a stop, once its controller is online (§8.3 rule 9).
    403 for a Viewer (§13)."""
    farm = read_farm(pack)
    now = datetime.now(UTC)
    if body.zone is not None:
        zone = next((z for z in farm.zones if z.id == body.zone), None)
        if zone is None:
            raise HTTPException(404, f"There is no zone {body.zone}")
        controller, command = zone.controller, messages.resume_zone(site.site_id, zone.id, now, caller.issued_by)
    else:
        controller, command = farm.climate.controller, messages.resume_climate(site.site_id, now, caller.issued_by)
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
    answer = _send(publisher, command)
    sent.record(engine, site, command, datetime.now(UTC), user_id=caller.user_id, role=caller.role)
    return answer


def post_confirm(
    request_id: Annotated[str, Path(alias="id")],
    pack: PackDep,
    engine: EngineDep,
    site: SiteDep,
    publisher: PublisherDep,
    caller: MayDose,
    idempotency_key: Annotated[uuid.UUID | None, Header(alias="Idempotency-Key")] = None,
) -> CommandSent:
    """Send a reviewed dose (§11.3), once it passes its checks again. The same Idempotency-Key
    again gets the same cmdId, and never a second dose (§11.1). 403 for a Viewer (§13)."""
    key = None if idempotency_key is None else str(idempotency_key)
    return confirm(pack, engine, site, publisher, request_id, key, caller)


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
        ("/api/stop", post_stop, (401, 403, 404, 422, 503)),
        ("/api/resume", post_resume, (401, 403, 404, 409, 422, 503)),
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
    app.add_api_route(
        "/api/dose-requests/{id}/confirm",
        post_confirm,
        methods=["POST"],
        status_code=202,
        response_model=CommandSent,
        responses=_errors(401, 403, 404, 409, 422, 500, 503),
        tags=["dosing"],
    )
