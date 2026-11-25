"""POST /api/dose-requests: the safety review of a dose before anyone confirms it (contract §11.3).

It reads the zone's latest state as GET /api/zones/{zoneId} answers it, its settings from the config
pack and its 24-hour totals, runs app.api.dosing.review, and saves the answer for the confirm step.
Nothing is sent to the farm.

GET /api/zones/{zoneId}/dose-limits gives the same limits and 24-hour totals before a review, so
the app can show them while the amounts are typed (T1-37). The review still decides.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Path
from sqlalchemy import Connection, insert, select
from sqlalchemy.dialects.postgresql import insert as upsert

from ..auth import audit
from ..auth.access import AGRONOMIST, OWNER, STAFF, Caller, allowed
from ..db.models import DoseRequest, StockGrade
from ..errors import ApiError
from . import queries
from .dosing import DoseInputError, review, validate_lines
from .pack import Pack, SiteRef, ZoneDosing, read_dosing, read_farm
from .routes import ZONE_ID, EngineDep, PackDep, SiteDep, _database, _errors, _not_loaded, _zone
from .schemas import (
    DoseLimits,
    DoseLineLimit,
    DoseLineMl,
    DoseRequestBody,
    DoseReview,
    FarmZone,
    Npk,
    StockNpk,
    ZoneStatus,
)

DAY = timedelta(hours=24)

may_dose = allowed(*STAFF, why="Viewers can’t dose")  # §11.2: Owner, Agronomist, Operator
MayDose = Annotated[Caller, Depends(may_dose)]
# the stock's N-P-K is the agronomy's, as a recipe is (§13)
may_grade = allowed(OWNER, AGRONOMIST, why="Only Owners and Agronomists change a stock's N-P-K")
MayGrade = Annotated[Caller, Depends(may_grade)]


def _zone_and_dosing(pack: Pack, zone_id: str) -> tuple[FarmZone, ZoneDosing]:
    """The zone's layout and dosing settings from the config pack; 404 if it has no such zone."""
    no_such_zone = ApiError(404, "NOT_FOUND", f"There is no zone {zone_id}")
    zone = next((z for z in read_farm(pack).zones if z.id == zone_id), None)
    if zone is None or not ZONE_ID.fullmatch(zone_id):
        raise no_such_zone
    dosing = read_dosing(pack, zone.id)
    if dosing is None:
        raise no_such_zone
    return zone, dosing


def _status(connection: Connection, site: SiteRef, zone: FarmZone) -> ZoneStatus:
    """The zone's GET /api/zones/{zoneId} answer; 503 before the site is loaded."""
    rows = queries.zones(connection, site, zone.id)
    if not rows:
        if not queries.site_is_loaded(connection, site):
            raise _not_loaded()
        raise ApiError(404, "NOT_FOUND", f"There is no zone {zone.id}")
    return _zone(connection, site, rows[0], zone.source)


def _dosed_24h(
    connection: Connection,
    site: SiteRef,
    zone: FarmZone,
    dosing: ZoneDosing,
    status: ZoneStatus,
    now: datetime,
) -> dict[str, float]:
    """Delivered mL per line in the last 24 hours. A simulated zone on a playground site follows
    simulated time (§11.4): its newest simTs is the farm's "now" there. A live zone's are on real
    time, whatever the site."""
    farm_now = status.sim_ts if dosing.simulated_time and status.sim_ts is not None else now
    return queries.delivered_ml_since(
        connection, site, zone.controller, farm_now - DAY, simulated=dosing.simulated_time
    )


def create_dose_request(
    body: DoseRequestBody, pack: PackDep, engine: EngineDep, site: SiteDep, caller: MayDose
) -> DoseReview:
    """Review a dose. 201 with every check, whatever they say; canConfirm tells if it may go on.
    403 for a Viewer (§13)."""
    zone, dosing = _zone_and_dosing(pack, body.zone)
    try:
        validate_lines(zone, body.lines)
    except DoseInputError as exc:
        raise ApiError(422, "INVALID_COMMAND", str(exc)) from exc

    now = datetime.now(UTC)
    with _database(engine) as connection:
        status = _status(connection, site, zone)
        dosed = _dosed_24h(connection, site, zone, dosing, status, now)
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
                requested_by=caller.user_id,
            )
        )
        audit.write(connection, site, caller.user_id, caller.role, "DOSE_REVIEW", answer.id,
                    {"zone": zone.id, "lines": saved["lines"], "canConfirm": answer.can_confirm})
        connection.commit()
    return answer


def get_dose_limits(
    zone_id: Annotated[str, Path(alias="zoneId")], pack: PackDep, engine: EngineDep, site: SiteDep
) -> DoseLimits:
    """Each line's per-dose and 24 h limits from the config pack, and how much of the 24 h limit is
    left now: the same numbers the review checks LIMIT_DOSE and LIMIT_24H against."""
    zone, dosing = _zone_and_dosing(pack, zone_id)
    per_dose = dosing.limits.get("maxPerDoseMl", {})
    per_day = dosing.limits.get("max24hMl", {})
    with _database(engine) as connection:
        status = _status(connection, site, zone)
        dosed = _dosed_24h(connection, site, zone, dosing, status, datetime.now(UTC))
        grades = _grades(connection, site)
    return DoseLimits(
        zone=zone.id,
        lines=[
            DoseLineLimit(
                line=line,
                **_about(dosing, line, grades.get(line)),
                max_per_dose_ml=per_dose.get(line, 0),
                max24h_ml=per_day.get(line, 0),
                dosed24h_ml=dosed.get(line, 0),
                left24h_ml=max(0, per_day.get(line, 0) - dosed.get(line, 0)),
            )
            for line in zone.dose_lines
        ],
    )


def _grades(connection: Connection, site: SiteRef) -> dict[str, Npk]:
    """The N-P-K grades set in the app, by dose line (stock_grade)."""
    rows = connection.execute(
        select(StockGrade.line, StockGrade.n, StockGrade.p, StockGrade.k)
        .where(StockGrade.org_id == site.org_id, StockGrade.site_id == site.site_id)
    )
    return {row.line: Npk(n=row.n, p=row.p, k=row.k) for row in rows}


def _about(dosing: ZoneDosing, line: str, grade: Npk | None = None) -> dict[str, Any]:
    """What the line doses and its pump rate (T1-38); null for what the pack doesn't say. `grade`,
    the N-P-K set in the app, stands over the pack's."""
    stock = dosing.stock.get(line)
    return {
        "name": stock.name if stock else None,
        "compound": stock.compound if stock else None,
        "strength_pct": stock.strength_pct if stock else None,
        "drum": stock.drum if stock else None,
        "purpose": stock.purpose if stock else None,
        "npk": grade or (stock.npk if stock else None),
        "pump_ml_per_min": dosing.pump_ml_per_min.get(line),
    }


def set_stock_npk(
    line: Annotated[str, Path(max_length=16)],
    body: Npk,
    pack: PackDep,
    engine: EngineDep,
    site: SiteDep,
    caller: MayGrade,
) -> StockNpk:
    """Set a stock's N-P-K grade, e.g. after a new product goes into its drum: Owner and Agronomist
    only; 404 for a line no zone doses. It stands over the pack's npk for every zone's dose form."""
    if line not in {name for zone in read_farm(pack).zones for name in zone.dose_lines}:
        raise ApiError(404, "NOT_FOUND", f"No zone doses a line {line}")
    with _database(engine) as connection:
        before = _grades(connection, site).get(line)
        if before is None:
            stock = read_dosing(pack, read_farm(pack).zones[0].id).stock.get(line)
            before = stock.npk if stock else None
        values = {"n": body.n, "p": body.p, "k": body.k, "updated_by": caller.user_id,
                  "updated_at": datetime.now(UTC)}
        connection.execute(
            upsert(StockGrade)
            .values(org_id=site.org_id, site_id=site.site_id, line=line, **values)
            .on_conflict_do_update(index_elements=["org_id", "site_id", "line"], set_=values)
        )
        audit.write(connection, site, caller.user_id, caller.role, "STOCK_NPK_CHANGED", line,
                    {"from": before.model_dump() if before else None, "to": body.model_dump()})
        connection.commit()
    return StockNpk(line=line, npk=body)


def add_dose_api(app: FastAPI) -> None:
    app.add_api_route(
        "/api/stocks/{line}/npk",
        set_stock_npk,
        methods=["PUT"],
        response_model=StockNpk,
        responses=_errors(401, 403, 404, 422, 503),
        tags=["dosing"],
    )
    app.add_api_route(
        "/api/dose-requests",
        create_dose_request,
        methods=["POST"],
        status_code=201,
        response_model=DoseReview,
        responses=_errors(401, 403, 404, 422, 500, 503),
        tags=["dosing"],
    )
    app.add_api_route(
        "/api/zones/{zoneId}/dose-limits",
        get_dose_limits,
        response_model=DoseLimits,
        responses=_errors(404, 422, 500, 503),
        tags=["dosing"],
    )
