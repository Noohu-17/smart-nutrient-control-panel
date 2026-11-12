"""The playground's /api/sim routes forward to the simulator's control API (T1-20, contract §11.4).

The simulator here is a fake: a small HTTP server on 127.0.0.1 that answers the §11.4 paths and
bodies the way the app's fake does (app/src/mocks/sim.ts), checks speeds against simulator.json and
drills and ranges against scenarios.json, and records every call it gets. Its paths are those
T4-9 gives the simulator: the /api/sim path without /api/sim, so GET /state for GET /api/sim/state. So each test goes over
real HTTP, through the same client the API uses with the real simulator. No database is needed:
the reset's record (T1-25) goes to a database that can't be reached, and test_playground_reset
checks it with a real one.
"""

import dataclasses
import json
import logging
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from test_read_api import FEATURES, write_pack

from app.api.routes import get_engine, get_settings
from app.api.sim import add_sim_api
from app.api.sim.client import HttpSimulator
from app.api.sim.routes import ROUTES, get_simulator
from app.config import load_settings
from app.main import app

REPO_PACK = load_settings().client_dir
SCENARIOS = json.loads((REPO_PACK / "scenarios.json").read_text(encoding="utf-8"))
SPEEDS = json.loads((REPO_PACK / "simulator.json").read_text(encoding="utf-8"))["speedOptions"]
# §11.4.3 lists the E-STOP drill. The fake also takes it, as the simulator will.
KINDS = {drill["kind"] for drill in SCENARIOS["failureDrills"]} | {"ESTOP"}
START = {"scenario": "Default farm", "simTime": "2026-07-01T00:30:00.000Z", "weather": "CLEAR"}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def refused(status: int, code: str, message: str, details: dict | None = None) -> tuple[int, dict]:
    error = {"code": code, "message": message} | ({"details": details} if details else {})
    return status, {"error": error}


class FakeSimulator:
    """The simulator's control API (T4-9) as §11.4 describes it, with answers in the §11.1 shape."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reply: tuple[int, bytes, dict[str, str]] | None = None  # answer this instead
        self.delay_s = 0.0
        self.reset()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.serve(self)

            do_POST = do_DELETE = do_PUT = do_PATCH = do_GET

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def reset(self):
        self.speed, self.faults, self.holds = 60, [], []

    def state(self) -> dict:
        return START | {"speed": self.speed, "faults": list(self.faults), "holds": list(self.holds)}

    def serve(self, request: BaseHTTPRequestHandler):
        raw = request.rfile.read(int(request.headers.get("Content-Length") or 0))
        self.calls.append({
            "method": request.command,
            "path": request.path,
            "headers": {name.lower(): value for name, value in request.headers.items()},
            "body": json.loads(raw) if raw else None,
        })
        time.sleep(self.delay_s)
        if self.reply is not None:
            status, payload, headers = self.reply
        else:
            status, answer = self.answer(request.command, request.path, self.calls[-1]["body"])
            payload, headers = json.dumps(answer).encode(), {"Content-Type": "application/json"}
        request.send_response(status)
        for name, value in headers.items():
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(payload)))
        request.end_headers()
        request.wfile.write(payload)

    def answer(self, method: str, path: str, body) -> tuple[int, dict]:
        match method, path.split("/")[1:]:
            case "GET", ["state"]:
                pass
            case "POST", ["speed"]:
                if body["x"] not in SPEEDS or isinstance(body["x"], bool):
                    return refused(422, "VALIDATION_ERROR", f"Speed must be one of {', '.join(map(str, SPEEDS))}")
                self.speed = body["x"]
            case "POST", ["reset"]:
                self.reset()
            case "POST", ["faults"]:
                if body["kind"] not in KINDS:
                    return refused(422, "VALIDATION_ERROR", f"There is no failure drill {body['kind']}")
                drill = {"kind": body["kind"], "target": body["target"]}
                self.faults = [f for f in self.faults if f != drill] + ([drill] if body["on"] else [])
            case "POST", ["holds"]:
                group = "polyhouse" if body["target"] == "POLYHOUSE" else "zone"
                limits = SCENARIOS["holdable"][group].get(body["metric"].split(".")[0])
                if limits is None:
                    return refused(422, "VALIDATION_ERROR", f"{body['metric']} can't be held on {body['target']}")
                if not limits["min"] <= body["value"] <= limits["max"]:
                    return refused(422, "VALIDATION_ERROR", f"{body['metric']} must be between "
                                   f"{limits['min']} and {limits['max']}", {"min": limits["min"], "max": limits["max"]})
                self.holds = [h for h in self.holds if (h["target"], h["metric"]) != (body["target"], body["metric"])]
                self.holds.append(body)
            case "POST", ["holds", "release-all"]:
                self.holds = []
            case "DELETE", ["holds", target, metric]:
                kept = [h for h in self.holds if (h["target"], h["metric"]) != (target, metric)]
                if kept == self.holds:
                    return refused(404, "NOT_FOUND", f"Nothing is held on {target} {metric}")
                self.holds = kept
            case _:
                return 404, {"detail": "Not Found"}
        return 200, self.state()


@pytest.fixture
def fake():
    simulator = FakeSimulator()
    yield simulator
    simulator.close()


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def sim_log(caplog):
    """api.sim keeps its lines to its own handler, so caplog listens there too."""
    logger = logging.getLogger("api.sim")
    logger.addHandler(caplog.handler)
    yield
    logger.removeHandler(caplog.handler)


def write_sim_pack(folder: Path, features: dict = FEATURES) -> Path:
    write_pack(folder, features=features)
    shutil.copy(REPO_PACK / "scenarios.json", folder / "scenarios.json")
    return folder


# Nothing listens on port 1, so recording a reset fails at once, as with the database down.
NO_DATABASE = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})


def client(pack: Path, url: str | None) -> TestClient:
    settings = dataclasses.replace(load_settings(), client_dir=pack, sim_control_url=url)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: NO_DATABASE
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def pack(tmp_path):
    return write_sim_pack(tmp_path / "client_A")


@pytest.fixture
def api(pack, fake):
    return client(pack, fake.url)


def error(response) -> tuple[int, str, str]:
    body = response.json()
    assert set(body) == {"error"}, body
    return response.status_code, body["error"]["code"], body["error"]["message"]


HOLD = {"target": "Z1", "metric": "ph", "value": 7.2, "gradual": True}

# (method, path on the API, body, the call the simulator gets)
FORWARDED = [
    ("GET", "/api/sim/state", None, ("GET", "/state", None)),
    ("POST", "/api/sim/speed", {"x": 288}, ("POST", "/speed", {"x": 288})),
    ("POST", "/api/sim/reset", None, ("POST", "/reset", None)),
    ("POST", "/api/sim/faults", {"kind": "LEAK", "target": "Z1", "on": True},
     ("POST", "/faults", {"kind": "LEAK", "target": "Z1", "on": True})),
    ("POST", "/api/sim/holds", HOLD, ("POST", "/holds", HOLD)),
    ("POST", "/api/sim/holds/release-all", None, ("POST", "/holds/release-all", None)),
]
ALL_ROUTES = [(method, path, body) for method, path, body, _ in FORWARDED] + [
    ("DELETE", "/api/sim/holds/Z1/ph", None),
    ("GET", "/api/sim/scenario-options", None),
]


def call(api: TestClient, method: str, path: str, body=None):
    return api.request(method, path, json=body)


@pytest.mark.parametrize(("method", "path", "body", "sent"), FORWARDED, ids=[f[1] for f in FORWARDED])
def test_each_route_reaches_the_simulator_and_brings_back_its_answer(api, fake, method, path, body, sent):
    response = call(api, method, path, body)
    assert [(c["method"], c["path"], c["body"]) for c in fake.calls] == [sent]
    assert response.status_code == 200
    assert response.json() == fake.state()


def test_speed_a_drill_a_hold_and_reset_show_in_the_next_state(api, fake):
    api.post("/api/sim/speed", json={"x": 288})
    api.post("/api/sim/faults", json={"kind": "PROBE_DRIFT", "target": "Z1.ph", "on": True})
    api.post("/api/sim/faults", json={"kind": "ESTOP", "target": "Z2", "on": True})
    api.post("/api/sim/holds", json={"target": "Z1", "metric": "reservoirPct", "value": 12})
    state = api.get("/api/sim/state").json()
    assert state["speed"] == 288
    assert state["faults"] == [{"kind": "PROBE_DRIFT", "target": "Z1.ph"}, {"kind": "ESTOP", "target": "Z2"}]
    assert state["holds"] == [{"target": "Z1", "metric": "reservoirPct", "value": 12, "gradual": True}]

    api.post("/api/sim/faults", json={"kind": "ESTOP", "target": "Z2", "on": False})  # released
    assert api.delete("/api/sim/holds/Z1/reservoirPct").json()["holds"] == []
    api.post("/api/sim/holds", json={"target": "POLYHOUSE", "metric": "airTempC", "value": 38})
    api.post("/api/sim/holds", json={"target": "Z2", "metric": "dayTankPct.ACID", "value": 5})
    assert len(api.get("/api/sim/state").json()["holds"]) == 2
    assert api.post("/api/sim/holds/release-all").json()["holds"] == []
    assert api.get("/api/sim/state").json()["faults"] == [{"kind": "PROBE_DRIFT", "target": "Z1.ph"}]

    reset = api.post("/api/sim/reset").json()
    assert (reset["speed"], reset["faults"], reset["holds"]) == (60, [], [])


def test_a_hold_is_gradual_unless_it_says_otherwise(api, fake):
    api.post("/api/sim/holds", json={"target": "Z1", "metric": "ec", "value": 3})
    api.post("/api/sim/holds", json={"target": "Z1", "metric": "ph", "value": 5.5, "gradual": False})
    assert [c["body"]["gradual"] for c in fake.calls] == [True, False]


def test_a_drill_value_is_sent_only_when_given(api, fake):
    api.post("/api/sim/faults", json={"kind": "LEAK", "target": "Z1", "on": True})
    api.post("/api/sim/faults", json={"kind": "ZONE_MODE", "target": "Z1", "on": True, "value": "MAINT"})
    assert [c["body"] for c in fake.calls] == [
        {"kind": "LEAK", "target": "Z1", "on": True},
        {"kind": "ZONE_MODE", "target": "Z1", "on": True, "value": "MAINT"},
    ]


def test_numbers_go_on_as_they_came(api, fake):
    api.post("/api/sim/speed", json={"x": 60})
    api.post("/api/sim/holds", json={"target": "Z1", "metric": "ph", "value": 7})
    api.post("/api/sim/holds", json={"target": "Z1", "metric": "ec", "value": 2.25})
    x, ph, ec = (c["body"].get("x", c["body"].get("value")) for c in fake.calls)
    assert (x, type(x), ph, type(ph), ec) == (60, int, 7, int, 2.25)


def test_fields_the_api_does_not_know_never_reach_the_simulator(api, fake):
    api.post("/api/sim/faults", json={"kind": "LEAK", "target": "Z1", "on": True, "url": "http://elsewhere"})
    api.post("/api/sim/speed", json={"x": 1, "site": "another-site"})
    assert [c["body"] for c in fake.calls] == [{"kind": "LEAK", "target": "Z1", "on": True}, {"x": 1}]


def test_a_refusal_comes_back_as_the_simulator_gave_it(api, fake):
    assert error(api.post("/api/sim/speed", json={"x": 5})) == (
        422, "VALIDATION_ERROR", "Speed must be one of 0, 1, 60, 288")
    assert error(api.delete("/api/sim/holds/Z1/ph")) == (404, "NOT_FOUND", "Nothing is held on Z1 ph")
    too_high = api.post("/api/sim/holds", json={"target": "Z1", "metric": "ph", "value": 14.5})
    assert too_high.status_code == 422
    assert too_high.json() == {"error": {
        "code": "VALIDATION_ERROR", "message": "ph must be between 0 and 14", "details": {"min": 0, "max": 14},
    }}


def answer_with(fake: FakeSimulator, status: int, body, content_type: str = "application/json"):
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()
    fake.reply = (status, payload, {"Content-Type": content_type})


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (409, {"error": {"code": "SANDBOX_LIMIT", "message": "Too many farms"}}, (409, "SANDBOX_LIMIT", "Too many farms")),
        (422, {"error": {"code": "VALIDATION_ERROR", "message": "No", "details": {}}}, (422, "VALIDATION_ERROR", "No")),
        (409, {"detail": "Busy resetting"}, (409, "CONFLICT", "Busy resetting")),
        (422, {"detail": [{"loc": ["body", "x"], "msg": "bad"}]},
         (422, "VALIDATION_ERROR", "The simulator refused the request")),
        (400, b"", (400, "BAD_REQUEST", "The simulator refused the request")),
        (404, {"error": {"code": "not a code", "message": "Nothing there"}}, (404, "NOT_FOUND", "Nothing there")),
        (404, {"error": {"code": "NOT_FOUND", "message": 7}}, (404, "NOT_FOUND", "The simulator refused the request")),
        (422, {"error": {"code": "VALIDATION_ERROR", "message": "x" * 900}}, (422, "VALIDATION_ERROR", "x" * 500)),
        (422, ["not", "an", "object"], (422, "VALIDATION_ERROR", "The simulator refused the request")),
    ],
    ids=["own code", "empty details", "FastAPI shape", "FastAPI list", "empty", "bad code", "no message", "long", "a list"],
)
def test_a_refusal_in_any_shape_comes_back_in_the_11_1_shape(api, fake, status, body, expected):
    answer_with(fake, status, body)
    assert error(api.post("/api/sim/speed", json={"x": 1})) == expected


def test_a_refusal_with_field_details_comes_back_whole(api, fake):
    # as T4-9's control API refuses a value (simulator/sim/playground.py)
    fields = {"fields": [{"field": "body.x", "message": "Speed must be one of 0, 1, 60, 288"}]}
    answer_with(fake, 422, {"error": {"code": "VALIDATION_ERROR", "message": "Speed must be one of 0, 1, 60, 288",
                                      "details": fields}})
    assert api.post("/api/sim/speed", json={"x": 1}).json() == {"error": {
        "code": "VALIDATION_ERROR", "message": "Speed must be one of 0, 1, 60, 288", "details": fields}}


def test_refusal_details_are_passed_on_only_as_an_object(api, fake):
    answer_with(fake, 422, {"error": {"code": "VALIDATION_ERROR", "message": "No", "details": ["a"]}})
    assert api.post("/api/sim/speed", json={"x": 1}).json() == {"error": {"code": "VALIDATION_ERROR", "message": "No"}}


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [
        (500, {"detail": "Traceback: secret"}, "application/json"),
        (401, {"detail": "who?"}, "application/json"),
        (403, {"detail": "no"}, "application/json"),
        (405, {"detail": "Method Not Allowed"}, "application/json"),
        (200, b"<html>not json</html>", "text/html"),
        (200, b'{"speed": NaN}', "application/json"),
        (200, b'{"speed": 60', "application/json"),
        (200, b"\xff\xfe", "application/json"),
        (200, b'"' + b"x" * 999_999 + b'"', "application/json"),  # 1 byte over, and whole JSON
    ],
    ids=["500", "401", "403", "405", "html", "NaN", "cut short", "not utf-8", "too big"],
)
def test_an_answer_that_cannot_be_passed_on_is_a_500_with_no_detail(api, fake, status, body, content_type, caplog):
    answer_with(fake, status, body, content_type)
    with caplog.at_level(logging.ERROR, logger="api.sim"):
        assert error(api.get("/api/sim/state")) == (500, "INTERNAL", "Something went wrong on the server")
    assert "GET /state" in caplog.text


def test_an_answer_of_exactly_the_size_limit_is_passed_back(api, fake):
    answer_with(fake, 200, b'"' + b"x" * 999_998 + b'"')  # 1,000,000 bytes
    response = api.get("/api/sim/state")
    assert (response.status_code, len(response.json())) == (200, 999_998)


@pytest.mark.parametrize(
    ("status", "method", "path"),
    [(302, "GET", "/api/sim/state"), (307, "GET", "/api/sim/state"), (303, "POST", "/api/sim/reset"),
     (301, "POST", "/api/sim/holds/release-all"), (308, "DELETE", "/api/sim/holds/Z1/ph")],
)
def test_a_redirect_is_never_followed(api, fake, status, method, path):
    other = FakeSimulator()
    try:
        fake.reply = (status, b"", {"Location": f"{other.url}/state"})
        assert error(api.request(method, path))[:2] == (500, "INTERNAL")
        assert other.calls == []
    finally:
        other.close()


def test_the_simulators_other_successes_are_passed_back_as_they_are(api, fake):
    answer_with(fake, 202, {"accepted": True})
    accepted = api.post("/api/sim/reset")
    assert (accepted.status_code, accepted.json()) == (202, {"accepted": True})
    answer_with(fake, 204, b"")
    nothing = api.post("/api/sim/holds/release-all")
    assert (nothing.status_code, nothing.content) == (204, b"")
    answer_with(fake, 200, b"")
    empty = api.post("/api/sim/holds/release-all")
    assert (empty.status_code, empty.content) == (200, b"")


def test_a_simulator_that_is_not_ready_is_a_503(api, fake):
    answer_with(fake, 503, {"detail": "starting"})
    assert error(api.get("/api/sim/state")) == (503, "SERVICE_UNAVAILABLE", "The simulator is not available right now")


def test_a_simulator_that_is_not_running_is_a_503_at_once(pack):
    api = client(pack, f"http://127.0.0.1:{free_port()}")
    started = time.monotonic()
    assert error(api.post("/api/sim/speed", json={"x": 1})) == (
        503, "SERVICE_UNAVAILABLE", "The simulator is not available right now")
    assert time.monotonic() - started < 3


def test_a_simulator_that_does_not_answer_in_time_is_a_503(api, fake):
    app.dependency_overrides[get_simulator] = lambda: HttpSimulator(fake.url, timeout_s=0.3)
    fake.delay_s = 1.0
    assert error(api.post("/api/sim/reset")) == (
        503, "SERVICE_UNAVAILABLE", "The simulator did not answer in time, so the change may or may not have been made")
    assert error(api.get("/api/sim/state")) == (503, "SERVICE_UNAVAILABLE", "The simulator did not answer in time")


@pytest.mark.parametrize("url", [None, "ftp://127.0.0.1:21", "127.0.0.1:8100", "http://sim:8100/?x=1"])
def test_without_a_usable_simulator_address_the_routes_answer_503(pack, url, caplog):
    api = client(pack, url)
    with caplog.at_level(logging.ERROR, logger="api.sim"):
        assert error(api.get("/api/sim/state")) == (
            503, "SERVICE_UNAVAILABLE", "The simulator is not set up for this site")
    assert "SIM_CONTROL_URL" in caplog.text


@pytest.mark.parametrize(
    "features", [{"playground": False}, {"demoFarm": True}], ids=["playground false", "no playground switch"]
)
@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES, ids=[r[1] + " " + r[0] for r in ALL_ROUTES])
def test_a_site_without_the_playground_answers_404_and_sends_nothing(tmp_path, fake, features, method, path, body):
    api = client(write_sim_pack(tmp_path / "client_B", features), fake.url)
    assert error(call(api, method, path, body)) == (404, "NOT_FOUND", "This site has no simulator")
    assert fake.calls == []


def test_without_the_playground_even_a_bad_body_is_a_404(tmp_path, fake):
    api = client(write_sim_pack(tmp_path / "client_B", {"playground": False}), fake.url)
    assert error(api.post("/api/sim/speed", json={"x": "fast"}))[0] == 404
    assert error(api.delete("/api/sim/holds/Z1 x/ph"))[0] == 404


@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/api/sim/speed", {"x": "60"}, "body.x"),
        ("/api/sim/speed", {"x": True}, "body.x"),
        ("/api/sim/speed", {}, "body.x"),
        ("/api/sim/faults", {"kind": "LEAK", "target": "Z1", "on": "yes"}, "body.on"),
        ("/api/sim/faults", {"kind": "LEAK", "target": "../reset", "on": True}, "body.target"),
        ("/api/sim/faults", {"kind": "LEAK", "target": "Z1", "on": True, "value": "a/b"}, "body.value"),
        ("/api/sim/faults", {"kind": "L" * 65, "target": "Z1", "on": True}, "body.kind"),
        ("/api/sim/holds", {"target": "Z1", "metric": "ph", "value": "7"}, "body.value"),
        ("/api/sim/holds", {"target": "Z1", "metric": "ph?x=1", "value": 7}, "body.metric"),
        ("/api/sim/holds", {"target": "Z1", "metric": "ph", "value": 7, "gradual": 1}, "body.gradual"),
    ],
)
def test_a_body_of_the_wrong_shape_is_a_422_and_nothing_is_sent(api, fake, path, body, field):
    response = api.post(path, json=body)
    assert error(response)[:2] == (422, "VALIDATION_ERROR")
    assert field in [f["field"] for f in response.json()["error"]["details"]["fields"]]
    assert fake.calls == []


def test_not_a_number_is_refused_before_it_is_sent(api, fake):
    response = api.post("/api/sim/speed", content=b'{"x": NaN}', headers={"Content-Type": "application/json"})
    assert error(response)[:2] == (422, "VALIDATION_ERROR")
    assert fake.calls == []


@pytest.mark.parametrize(
    "path",
    ["/api/sim/holds/Z1%20x/ph", "/api/sim/holds/Z1/ph%3Fx%3D1", "/api/sim/holds/..%2Freset/x",
     "/api/sim/holds/../reset", "/api/sim/holds/Z1/ph/more", "/api/sim/holds/.hidden/ph"],
)
def test_a_release_path_can_only_name_one_held_value(api, fake, path):
    assert api.delete(path).status_code in (404, 405, 422)
    assert fake.calls == []


def test_a_release_names_the_held_value_exactly(api, fake):
    api.delete("/api/sim/holds/Z2/dayTankPct.ACID")
    assert fake.calls[0]["path"] == "/holds/Z2/dayTankPct.ACID"


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/api/sim/scenario"), ("GET", "/api/sim/scenarios"), ("GET", "/api/sim/anything"),
     ("GET", "/api/sim"), ("GET", "/api/sim/speed"), ("PUT", "/api/sim/holds"), ("POST", "/api/sim/holds/Z1/ph"),
     ("GET", "/api/sim/sim/state"), ("POST", "/api/sim/state")],
)
def test_no_other_call_reaches_the_simulator(api, fake, method, path):
    assert api.request(method, path, json={}).status_code in (404, 405)
    assert fake.calls == []


def test_nothing_from_the_apps_request_is_passed_on(api, fake):
    api.post(
        "/api/sim/speed",
        json={"x": 1},
        headers={"Cookie": "session=secret", "Authorization": "Bearer secret", "X-CSRF-Token": "secret",
                 "X-Forwarded-For": "203.0.113.9", "Idempotency-Key": "secret"},
    )
    headers = fake.calls[0]["headers"]
    assert "secret" not in json.dumps(headers)
    assert set(headers) <= {"accept", "accept-encoding", "content-type", "content-length", "host", "user-agent",
                            "connection"}


def test_proxy_settings_in_the_environment_are_not_used(api, fake, monkeypatch):
    nowhere = f"http://127.0.0.1:{free_port()}"
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, nowhere)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert api.get("/api/sim/state").status_code == 200
    assert len(fake.calls) == 1


def test_an_address_with_a_path_keeps_it(pack, fake):
    api = client(pack, fake.url + "/control/")
    api.post("/api/sim/reset")
    assert fake.calls[0]["path"] == "/control/reset"


def test_the_scenario_options_are_scenarios_json_without_its_header(pack):
    api = client(pack, None)  # read from the config pack: no simulator needed
    response = api.get("/api/sim/scenario-options")
    assert response.status_code == 200
    expected = {key: value for key, value in SCENARIOS.items() if key not in ("v", "_meta")}
    assert response.json() == expected
    assert {"failureDrills", "holdable", "thingsToTry"} <= set(response.json())


def test_scenario_options_drop_every_meta_block(pack):
    (pack / "scenarios.json").write_text(
        json.dumps({"v": 1, "_meta": {"note": "x"}, "holdable": {"_meta": {"note": "y"}, "zone": {}}}), encoding="utf-8")
    assert client(pack, None).get("/api/sim/scenario-options").json() == {"holdable": {"zone": {}}}


@pytest.mark.parametrize("content", [None, "{ not json", "[1, 2]"], ids=["missing", "not json", "not an object"])
def test_unreadable_scenario_options_are_a_500(pack, content, caplog):
    file = pack / "scenarios.json"
    file.unlink()
    if content is not None:
        file.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="api.sim"):
        assert error(client(pack, None).get("/api/sim/scenario-options")) == (
            500, "INTERNAL", "Something went wrong on the server")
    assert "scenarios.json" in caplog.text


def test_each_call_is_logged(api, fake, tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="api.sim"):
        api.post("/api/sim/holds", json={"target": "Z1", "metric": "ph", "value": 7.2})
        api.post("/api/sim/speed", json={"x": 5})
        api.get("/api/sim/scenario-options")
        client(write_sim_pack(tmp_path / "client_B", {"playground": False}), fake.url).post("/api/sim/reset")
        client(write_sim_pack(tmp_path / "client_C"), f"http://127.0.0.1:{free_port()}").get("/api/sim/state")
    lines = [record.getMessage() for record in caplog.records if record.name == "api.sim"]
    assert any(line.startswith('POST /holds {"target":"Z1","metric":"ph","value":7.2,"gradual":true} -> 200')
               for line in lines), lines
    assert any(line.startswith('POST /speed {"x":5} -> 422 VALIDATION_ERROR') and "Speed must be" in line
               for line in lines), lines
    assert "GET /api/sim/scenario-options, from scenarios.json" in lines
    assert "POST /api/sim/reset refused: this site has no playground" in lines
    assert any(line.startswith("GET /state not sent:") for line in lines), lines


def test_text_from_outside_cannot_start_a_log_line_of_its_own(api, fake, tmp_path, caplog):
    forged = "No\nINFO api.sim: forged"
    answer_with(fake, 422, {"error": {"code": "VALIDATION_ERROR", "message": forged}})
    with caplog.at_level(logging.INFO, logger="api.sim"):
        assert error(api.post("/api/sim/reset"))[2] == forged  # the app gets it as it was sent
        client(write_sim_pack(tmp_path / "client_B", {"playground": False}), fake.url).delete(
            "/api/sim/holds/Z1%0AINFO%20forged/ph")
    lines = [record.getMessage() for record in caplog.records if record.name == "api.sim"]
    assert len(lines) == 2 and all("\n" not in line for line in lines), lines


def test_a_long_answer_is_shortened_in_the_log(api, fake, caplog):
    answer_with(fake, 500, {"detail": "x" * 5000})
    with caplog.at_level(logging.ERROR, logger="api.sim"):
        api.post("/api/sim/reset")
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("POST /reset"))
    assert line.endswith("…") and len(line) < 400


def test_openapi_lists_the_routes_with_the_11_1_error_body():
    paths = TestClient(app).get("/api/openapi.json").json()["paths"]
    sim = {(method.upper(), path) for path, item in paths.items() if path.startswith("/api/sim") for method in item}
    assert sim == {(method, path) for method, path, _, _ in ROUTES}
    for (method, path) in sim:
        operation = paths[path][method.lower()]
        assert operation["tags"] == ["simulator"]
        assert operation["responses"]["404"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorBody"}


SERVER = {"APP_ENV": "staging", "DB_PASSWORD": "a-real-password"}


@pytest.mark.parametrize(
    ("env", "url"),
    [
        ({}, "http://127.0.0.1:8100"),  # dev: where T4-9's control API listens on the laptop
        ({"SIM_CONTROL_URL": ""}, "http://127.0.0.1:8100"),
        ({"SIM_CONTROL_URL": "http://simulator:8100"}, "http://simulator:8100"),
        (SERVER, None),  # a server never falls back to a dev address
        (SERVER | {"SIM_CONTROL_URL": ""}, None),
        (SERVER | {"SIM_CONTROL_URL": "http://simulator:8100"}, "http://simulator:8100"),
    ],
)
def test_the_setting_is_read_from_the_environment(env, url):
    assert load_settings(env).sim_control_url == url


@pytest.mark.parametrize("url", ["ftp://x", "simulator:8100", "http://", "http://sim:8100/#x", "http://sim:8100/?a=1"])
def test_the_client_refuses_an_address_it_should_not_call(url):
    with pytest.raises(ValueError, match="SIM_CONTROL_URL"):
        HttpSimulator(url)


def test_the_call_log_has_one_handler_of_its_own_at_info():
    logger = logging.getLogger("api.sim")
    add_sim_api(FastAPI())  # building the app again adds no second handler
    own = [h for h in logger.handlers if type(h) is logging.StreamHandler]
    assert len(own) == 1 and own[0].stream is not None
    assert (logger.level, logger.propagate) == (logging.INFO, False)
    assert own[0].formatter._fmt == "%(asctime)s %(levelname)s %(name)s: %(message)s"
