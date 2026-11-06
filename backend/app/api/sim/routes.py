"""The playground's /api/sim routes (contract §11.4, T1-20).

Each route forwards to the simulator's own control API (T4-9) at SIM_CONTROL_URL, which nothing
outside the server can reach, on the same path without /api/sim (GET /api/sim/state goes to GET
/state). Only these routes are forwarded, never any other path. GET /api/sim/scenario-options is
read from the config pack's scenarios.json, the file the simulator checks against (§11.4.1). They
exist only on sites whose features.json has "playground": true: elsewhere every one answers 404.
Each call is logged on the api.sim logger, with its body and the simulator's answer. Roles (§11.4)
wait for sign-in.

Plain functions, so FastAPI runs them in its thread pool while they wait for the simulator.
"""

import json
import logging
import re
import time
from functools import cache
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import JSONResponse, Response

from ...errors import ApiError, _error
from ..pack import PackError, _read_json, _without_meta, read_farm
from ..routes import SettingsDep, _errors
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


def playground_only(request: Request, settings: SettingsDep) -> None:
    """404 on a site without the playground, before the body's fields are checked."""
    if read_farm(settings.client_dir).features.get("playground") is not True:
        log.info("%s %s refused: this site has no playground", request.method, _one_line(request.url.path))
        raise HTTPException(404, "This site has no simulator")


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


def get_state(simulator: SimulatorDep) -> Response:
    """The current scenario, simulated clock, speed, weather, running drills and held values."""
    return _forward(simulator, "GET", "/state")


def post_speed(body: SpeedBody, simulator: SimulatorDep) -> Response:
    """Clock speed: 0 (pause), 1, 60 or 288."""
    return _forward(simulator, "POST", "/speed", _body(body))


def post_reset(simulator: SimulatorDep) -> Response:
    """Back to the start of the current scenario: all drills off, levels and readings reset."""
    return _forward(simulator, "POST", "/reset")


def post_fault(body: FaultBody, simulator: SimulatorDep) -> Response:
    """Trigger ("on": true) or Fix ("on": false) a failure drill (§11.4.3)."""
    return _forward(simulator, "POST", "/faults", _body(body))


def post_hold(body: HoldBody, simulator: SimulatorDep) -> Response:
    """Set a value and hold it (§11.4.2)."""
    return _forward(simulator, "POST", "/holds", _body(body))


def delete_hold(
    target: Annotated[SimId, Path()], metric: Annotated[SimId, Path()], simulator: SimulatorDep
) -> Response:
    """Release one held value."""
    return _forward(simulator, "DELETE", f"/holds/{quote(target, safe='')}/{quote(metric, safe='')}")


def post_release_all(simulator: SimulatorDep) -> Response:
    """Release every held value."""
    return _forward(simulator, "POST", "/holds/release-all")


def get_scenario_options(settings: SettingsDep) -> dict[str, Any]:
    """scenarios.json without its file header: the drills, holdable ranges and setup options."""
    try:
        options = _read_json(settings.client_dir / "scenarios.json")
        if not isinstance(options, dict):
            raise TypeError("scenarios.json is not a JSON object")
    except (OSError, TypeError, ValueError) as exc:
        log.error("cannot load scenarios.json: %s", exc)
        raise PackError("The scenario options could not be loaded") from exc
    log.info("GET /api/sim/scenario-options, from scenarios.json")
    return {key: value for key, value in _without_meta(options).items() if key != "v"}


FORWARDED = (400, 404, 409, 422, 500, 503)

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
