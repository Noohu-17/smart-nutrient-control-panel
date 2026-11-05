"""POST /api/dose-requests: the safety review of a dose before anyone confirms it (contract §11.3).

It reads the zone's latest state as GET /api/zones/{zoneId} answers it, its settings from the config
pack and its 24-hour totals, runs app.api.dosing.review, and saves the answer for the confirm step.
Nothing is sent to the farm.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import insert

from ..db.models import DoseRequest
from ..errors import ApiError
from . import queries
from .dosing import DoseInputError, review, validate_lines
from .pack import read_dosing, read_farm
from .routes import ZONE_ID, EngineDep, SettingsDep, SiteDep, _database, _errors, _not_loaded, _zone
from .schemas import DoseLineMl, DoseRequestBody, DoseReview

DAY = timedelta(hours=24)


def create_dose_request(
    body: DoseRequestBody, settings: SettingsDep, engine: EngineDep, site: SiteDep
) -> DoseReview:
    """Review a dose. 201 with every check, whatever they say; canConfirm tells if it may go on."""
    no_such_zone = ApiError(404, "NOT_FOUND", f"There is no zone {body.zone}")
    zone = next((z for z in read_farm(settings.client_dir).zones if z.id == body.zone), None)
    if zone is None or not ZONE_ID.fullmatch(body.zone):
        raise no_such_zone
    dosing = read_dosing(settings.client_dir, zone.id)
    if dosing is None:
        raise no_such_zone
    try:
        validate_lines(zone, body.lines)
    except DoseInputError as exc:
        raise ApiError(422, "INVALID_COMMAND", str(exc)) from exc

    now = datetime.now(UTC)
    with _database(engine) as connection:
        rows = queries.zones(connection, site, zone.id)
        if not rows:
            if not queries.site_is_loaded(connection, site):
                raise _not_loaded()
            raise no_such_zone
        status = _zone(connection, site, rows[0])
        # 24 h totals follow simulated time on playground sites (§11.4): the zone's newest simTs
        # is the farm's "now" there.
        farm_now = status.sim_ts if dosing.playground and status.sim_ts is not None else now
        dosed = queries.delivered_ml_since(
            connection, site, zone.controller, farm_now - DAY, simulated=dosing.playground
        )
        result = review(zone, status, dosing, body.lines, dosed, now)
        answer = DoseReview(
            id=f"dr_{uuid.uuid4().hex}",
            zone=zone.id,
            lines=[DoseLineMl(line=a.line, ml=round(a.ml)) for a in body.lines],
            projected=result.projected,
            checks=result.checks,
            estimate=result.estimate,
            can_confirm=result.can_confirm,
        )
        saved = answer.model_dump(mode="json")
        connection.execute(
            insert(DoseRequest).values(
                org_id=site.org_id,
                site_id=site.site_id,
                request_id=answer.id,
                zone_id=zone.id,
                created_at=now,
                lines=saved["lines"],
                projected=saved["projected"],
                checks=saved["checks"],
                estimate=saved["estimate"],
                can_confirm=answer.can_confirm,
                limits=dosing.limits,
            )
        )
        connection.commit()
    return answer


def add_dose_api(app: FastAPI) -> None:
    app.add_api_route(
        "/api/dose-requests",
        create_dose_request,
        methods=["POST"],
        status_code=201,
        response_model=DoseReview,
        responses=_errors(404, 422, 500, 503),
        tags=["dosing"],
    )
