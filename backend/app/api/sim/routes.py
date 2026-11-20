"""The playground's /api/sim routes (contract §11.4, T1-20).

Each route forwards to the simulator's own control API (T4-9) at SIM_CONTROL_URL, which nothing
outside the server can reach, on the same path without /api/sim (GET /api/sim/state goes to GET
/state). Only these routes are forwarded, never any other path. GET /api/sim/scenario-options is
read from the config pack's scenarios.json, the file the simulator checks against (§11.4.1). They
exist only on sites whose features.json has "playground": true: elsewhere every one answers 404.
On a playground site, a hold or drill aimed at a LIVE zone (layout.json source, §2.4), or at its
controller, is refused with 409 LIVE_ZONE and never reaches the simulator, and so is a reset while
any zone is live (T1-33): real hardware runs those zones, on real time.
Each call is logged on the api.sim logger, with its body and the simulator's answer. Reading is
for every signed-in role; changing the farm is for Owner, Agronomist and Operator, and a reset for
the Owner only (§11.4). Each change the simulator accepts is in the audit log, with who made it. A
reset the simulator accepted is also recorded in the database, so the 24-hour
totals start again (§11.4, T1-25).

Plain functions, so FastAPI runs them in its thread pool while they wait for the simulator.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from ... import alerts
from ...auth import audit
from ...auth.access import OWNER, STAFF, Caller, allowed
from ...db.models import PlaygroundReset
from ...errors import ApiError, _error
from ..pack import PackError, SiteRef, _without_meta, read_farm
from ..routes import EngineDep, PackDep, SettingsDep, SiteDep, _errors
from ..schemas import Farm
from .client import BadAnswer, HttpSimulator, SimAnswer, Simulator, SimulatorDown, SimulatorTimedOut
from .schemas import FaultBody, HoldBody, SimId, SpeedBody

log = logging.getLogger("api.sim")

# The simulator refused the call: its answer goes back to the app, with this code if it gave none.
PASSED_ON = {400: "BAD_REQUEST", 404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR"}
CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")  # a code as §8.4 and §11.1 write them
MAX_MESSAGE = 500
MAX_LOGGED = 300

NOT_AVAILABLE = "The simulator is not available right now"
SERVER_ERROR = "Something went wrong on the server"  # §11.1 INTERNAL: no detail


def playground_only(request: Request, pack: PackDep) -> None:
    """404 on a site without the playground, before the body's fields are checked."""
    if read_farm(pack).features.get("playground") is not True:
        log.info("%s %s refused: this site has no playground", request.method, _one_line(request.url.path))
        raise HTTPException(404, "This site has no simulator")


def _live_zone_of(farm: Farm, target: str) -> str | None:
    """The LIVE zone a hold or drill target belongs to: the zone ("Z1"), a part of it ("Z1.ph",
    "Z1.ACID") or its zone controller ("ZC1"). None for a simulated zone, POLYHOUSE or a device
    of no zone."""
    live = {zone.id: zone for zone in farm.zones if zone.source == "LIVE"}
    zone_id = target.partition(".")[0]
    if zone_id in live:
        return zone_id
    return next((zone.id for zone in live.values() if zone.controller == target), None)


def refuse_live_target(pack: PackDep, target: str, what: str) -> None:
    """409 LIVE_ZONE for a hold or drill on a live zone: the simulator doesn't play it (T1-33)."""
    zone_id = _live_zone_of(read_farm(pack), target)
    if zone_id is not None:
        log.info("%s on %s refused: %s is a live zone", what, _one_line(target), zone_id)
        raise ApiError(409, "LIVE_ZONE", f"{zone_id} is a live zone: real hardware runs it, not the simulator")


@cache
def _simulator_at(url: str) -> HttpSimulator:
    return HttpSimulator(url)


def get_simulator(settings: SettingsDep) -> Simulator | None:
    """The simulator's control API, or None when SIM_CONTROL_URL is missing or not usable."""
    if settings.sim_control_url is None:
        return None
    try:
        return _simulator_at(settings.sim_control_url)
    except ValueError as exc:
        log.error("%s", exc)
        return None


SimulatorDep = Annotated[Simulator | None, Depends(get_simulator)]

may_play = allowed(*STAFF, why="Viewers can’t change the simulated farm")  # §11.4
may_reset = allowed(OWNER, why="Only an Owner can reset the simulated farm")  # §11.4
MayPlay = Annotated[Caller, Depends(may_play)]
MayReset = Annotated[Caller, Depends(may_reset)]


def _one_line(text: str) -> str:
    """Text from outside the API, such as a refusal message, can't start a log line of its own."""
    return " ".join(text.splitlines())


def _shown(value: Any) -> str:
    text = "" if value is None else json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return text if len(text) <= MAX_LOGGED else text[:MAX_LOGGED] + "…"


def _passed_back(answer: SimAnswer) -> Response:
    """The simulator's own answer, unchanged: its status and its JSON."""
    if answer.body is None or answer.status in (204, 205):
        return Response(status_code=answer.status)
    return JSONResponse(answer.body, status_code=answer.status)


def _refusal(answer: SimAnswer) -> tuple[str, str, dict[str, Any] | None]:
    """The code, message and details of a refusal, in the §11.1 shape whatever shape it came in."""
    body = answer.body if isinstance(answer.body, dict) else {}
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):  # already §11.1
        code, message, details = error.get("code"), error["message"], error.get("details")
    elif isinstance(body.get("detail"), str):  # FastAPI's own shape
        code, message, details = None, body["detail"], None
    else:
        code, message, details = None, "The simulator refused the request", None
    if not (isinstance(code, str) and CODE.fullmatch(code)):
        code = PASSED_ON[answer.status]
    return code, message[:MAX_MESSAGE], details if isinstance(details, dict) else None


def _forward(
    simulator: Simulator | None, method: str, path: str, body: dict[str, Any] | None = None
) -> Response:
    call = f"{method} {path} {_shown(body)}".rstrip()
    if simulator is None:
        log.error("%s not sent: SIM_CONTROL_URL is not set, or not usable", call)
        raise HTTPException(503, "The simulator is not set up for this site")
    started = time.monotonic()
    try:
        answer = simulator.send(method, path, body)
    except SimulatorDown as exc:
        log.warning("%s not sent: %s", call, exc)
        raise HTTPException(503, NOT_AVAILABLE) from exc
    except SimulatorTimedOut as exc:
        log.warning("%s sent, but no answer: %s", call, exc)
        message = "The simulator did not answer in time"
        if method != "GET":
            message += ", so the change may or may not have been made"
        raise HTTPException(503, message) from exc
    except BadAnswer as exc:
        log.error("%s: %s", call, exc)
        raise ApiError(500, "INTERNAL", SERVER_ERROR) from exc
    ms = round((time.monotonic() - started) * 1000)
    if 200 <= answer.status < 300:
        log.info("%s -> %d in %d ms", call, answer.status, ms)
        return _passed_back(answer)
    if answer.status in PASSED_ON:
        code, message, details = _refusal(answer)
        log.info("%s -> %d %s in %d ms: %s", call, answer.status, code, ms, _one_line(message))
        return _error(answer.status, code, message, details)
    if answer.status == 503:
        log.warning("%s -> 503 in %d ms: %s", call, ms, _shown(answer.body))
        raise HTTPException(503, NOT_AVAILABLE)
    shown = _shown(answer.body)
    log.error("%s -> %d in %d ms, which is not passed on: %s", call, answer.status, ms, shown)
    raise ApiError(500, "INTERNAL", SERVER_ERROR)


def _body(model: FaultBody | HoldBody | SpeedBody) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _audited(answer: Response, engine: Engine, site: SiteRef, caller: Caller, action: str,
             target: str | None = None, detail: dict[str, Any] | None = None) -> Response:
    """Add a change the simulator accepted to the audit log. It has happened already, so a database
    that can't take the row doesn't change the answer (audit.write_after logs it instead)."""
    if 200 <= answer.status_code < 300:
        audit.write_after(engine, site, caller.user_id, caller.role, action, target, detail)
    return answer


def get_state(simulator: SimulatorDep) -> Response:
    """The current scenario, simulated clock, speed, weather, running drills and held values."""
    return _forward(simulator, "GET", "/state")


def post_speed(body: SpeedBody, simulator: SimulatorDep, engine: EngineDep, site: SiteDep, caller: MayPlay) -> Response:
    """Clock speed: 0 (pause), 1, 60 or 288."""
    sent = _body(body)
    return _audited(_forward(simulator, "POST", "/speed", sent), engine, site, caller, "SIM_SPEED", detail=sent)


def record_reset(engine: Engine, site: SiteRef, at: datetime) -> None:
    """Keep when the farm was reset, so the dose review's 24-hour totals start again (§11.4), and
    clear every alert (T1-24).

    The reset has already happened on the simulator, so a database that can't take it doesn't undo
    it: the line says what that means instead.
    """
    statement = insert(PlaygroundReset).values(org_id=site.org_id, site_id=site.site_id, reset_at=at)
    try:
        with engine.begin() as connection:
            connection.execute(
                statement.on_conflict_do_update(index_elements=["org_id", "site_id"], set_={"reset_at": at})
            )
            alerts.clear_all(connection, site.org_id, site.site_id, at)  # T1-24
    except SQLAlchemyError as exc:
        first = str(getattr(exc, "orig", exc)).partition("\n")[0]
        log.error("reset not recorded, so the 24 h totals still count doses from before it: %s", first)
        return
    log.info("reset recorded: the 24 h totals start again from %s", at.isoformat())


def post_reset(
    pack: PackDep, simulator: SimulatorDep, engine: EngineDep, site: SiteDep, caller: MayReset
) -> Response:
    """Back to the start of the current scenario: all drills off, levels and readings reset, and
    the 24-hour totals start again. Owner only (§11.4). Refused while any zone is live: a reset is
    for the whole site, and a live zone's totals and alerts are real (T1-33)."""
    live = [zone.id for zone in read_farm(pack).zones if zone.source == "LIVE"]
    if live:
        log.info("POST /reset refused: live zones %s", ", ".join(live))
        raise ApiError(409, "LIVE_ZONE", f"This site has live zones ({', '.join(live)}): a reset is only for a simulated farm")
    answer = _forward(simulator, "POST", "/reset")
    if 200 <= answer.status_code < 300:
        # After the answer: a dose the reset stopped is reported before it, so it doesn't count.
        record_reset(engine, site, datetime.now(UTC))
    return _audited(answer, engine, site, caller, "SIM_RESET")


def post_fault(
    body: FaultBody, pack: PackDep, simulator: SimulatorDep, engine: EngineDep, site: SiteDep, caller: MayPlay
) -> Response:
    """Trigger ("on": true) or Fix ("on": false) a failure drill (§11.4.3); never on a live zone."""
    refuse_live_target(pack, body.target, f"POST /faults {body.kind}")
    sent = _body(body)
    return _audited(_forward(simulator, "POST", "/faults", sent), engine, site, caller, "SIM_FAULT", detail=sent)


def post_hold(
    body: HoldBody, pack: PackDep, simulator: SimulatorDep, engine: EngineDep, site: SiteDep, caller: MayPlay
) -> Response:
    """Set a value and hold it (§11.4.2); never on a live zone."""
    refuse_live_target(pack, body.target, "POST /holds")
    sent = _body(body)
    return _audited(_forward(simulator, "POST", "/holds", sent), engine, site, caller, "SIM_HOLD", detail=sent)


def delete_hold(
    target: Annotated[SimId, Path()],
    metric: Annotated[SimId, Path()],
    pack: PackDep,
    simulator: SimulatorDep,
    engine: EngineDep,
    site: SiteDep,
    caller: MayPlay,
) -> Response:
    """Release one held value; a live zone has none."""
    refuse_live_target(pack, target, "DELETE /holds")
    answer = _forward(simulator, "DELETE", f"/holds/{quote(target, safe='')}/{quote(metric, safe='')}")
    return _audited(answer, engine, site, caller, "SIM_RELEASE", detail={"target": target, "metric": metric})


def post_release_all(simulator: SimulatorDep, engine: EngineDep, site: SiteDep, caller: MayPlay) -> Response:
    """Release every held value."""
    return _audited(_forward(simulator, "POST", "/holds/release-all"), engine, site, caller, "SIM_RELEASE_ALL")


def get_scenario_options(pack: PackDep) -> dict[str, Any]:
    """scenarios.json without its file header: the drills, holdable ranges and setup options."""
    try:
        options = pack.json("scenarios.json")
        if not isinstance(options, dict):
            raise TypeError("scenarios.json is not a JSON object")
    except (OSError, TypeError, ValueError) as exc:
        log.error("cannot load scenarios.json: %s", exc)
        raise PackError("The scenario options could not be loaded") from exc
    log.info("GET /api/sim/scenario-options, from scenarios.json")
    return {key: value for key, value in _without_meta(options).items() if key != "v"}


FORWARDED = (400, 401, 403, 404, 409, 422, 500, 503)

ROUTES = (
    ("GET", "/api/sim/state", get_state, FORWARDED),
    ("GET", "/api/sim/scenario-options", get_scenario_options, (404, 500)),
    ("POST", "/api/sim/speed", post_speed, FORWARDED),
    ("POST", "/api/sim/reset", post_reset, FORWARDED),
    ("POST", "/api/sim/faults", post_fault, FORWARDED),
    ("POST", "/api/sim/holds", post_hold, FORWARDED),
    ("POST", "/api/sim/holds/release-all", post_release_all, FORWARDED),
    ("DELETE", "/api/sim/holds/{target}/{metric}", delete_hold, FORWARDED),
)


def _log_every_call() -> None:
    """uvicorn sets up only its own loggers, so api.sim gets a handler of its own: without one,
    a line below WARNING would go nowhere. It writes to stderr, as uvicorn does, in the worker's
    format, and once only, however many times the app is built."""
    if log.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False  # so a logging setup added later doesn't print every line twice


def add_sim_api(app: FastAPI) -> None:
    _log_every_call()
    for method, path, endpoint, errors in ROUTES:
        app.add_api_route(
            path,
            endpoint,
            methods=[method],
            dependencies=[Depends(playground_only)],
            responses=_errors(*errors),
            tags=["simulator"],
        )
