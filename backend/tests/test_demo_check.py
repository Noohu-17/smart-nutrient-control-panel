"""scripts/demo-check.py (T1-23): the demo scenarios, PASS or FAIL, through the API only.

The stack here is a fake: a small HTTP server answering the routes the script uses the way the
real back end and simulator do (contract §11.2, §11.3, §11.4), with a farm that moves one step
each time a command is looked at. Each test can break one thing, and the script must say FAIL for
that scenario, keep going with the others, and still put the farm back.
"""

import base64
import importlib.util
import io
import json
import socket
import subprocess
import sys
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.auth import totp

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "demo-check.py"
spec = importlib.util.spec_from_file_location("demo_check", SCRIPT)
demo_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo_check)

SCENARIOS = ["health", "fresh readings", "reset", "dose", "stop and resume", "unsafe dose", "sensor fault"]
START_SPEED = 1


def fresh_zone(zone_id):
    return {
        "zone": zone_id, "online": True, "stopped": False,
        "readings": {"ec": 1.1, "ph": 6.05, "reservoirPct": 78.0},
        "quality": {"ec": "OK", "ph": "OK", "reservoirPct": "OK"},
    }


class FakeStack:
    """The back end and the simulator as the script sees them through /api."""

    def __init__(self, *, playground=True, login=None, app_login=None, totp_secret=None, broken=()):
        """login: the site's (user, password); app_login: the app's (email, password), T1-35;
        totp_secret: the Owner's two-factor secret, when sign-in asks for a code."""
        self.playground, self.login, self.app_login, self.broken = playground, login, app_login, set(broken)
        self.totp_secret, self.signed_in = totp_secret, totp_secret is None
        self.calls: list[dict] = []
        self.reviews: dict[str, dict] = {}
        self.commands: dict[str, dict] = {}
        self.speed, self.faults, self.holds = 60, [], []
        self.zones = {z: fresh_zone(z) for z in ("Z1", "Z2")}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.serve(self)

            do_POST = do_DELETE = do_GET

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._lock = threading.Lock()
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    # --- HTTP ------------------------------------------------------------------------------

    def serve(self, request):
        raw = request.rfile.read(int(request.headers.get("Content-Length") or 0))
        body = json.loads(raw) if raw else None
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.calls.append({"method": request.command, "path": request.path, "headers": headers, "body": body})
        if "redirect" in self.broken:
            return self._send(request, 308, None, {"Location": "https://elsewhere.example/api/health"})
        if self.login is not None:
            expected = "Basic " + base64.b64encode(f"{self.login[0]}:{self.login[1]}".encode()).decode()
            if headers.get("authorization") != expected:
                return self._send(request, 401, None)
        if self.app_login is not None:
            if request.path == "/api/auth/login":
                if body != {"email": self.app_login[0], "password": self.app_login[1]}:
                    return self._send(request, 401, {"error": {"code": "UNAUTHORIZED", "message": "Wrong email or password"}})
                cookie = "sna_session=the-session; Path=/api; HttpOnly; Secure; SameSite=lax"
                step = "TOTP" if self.totp_secret else "DONE"
                answer = {"user": {"role": "OWNER"}, "csrfToken": "the-csrf", "step": step}
                return self._send(request, 200, answer, {"Set-Cookie": cookie})
            if request.path == "/api/auth/totp":
                if body != {"code": totp.code_at(self.totp_secret, totp.step_at(datetime.now(UTC)))}:
                    return self._send(request, 400, {"error": {"code": "WRONG_CODE", "message": "wrong code"}})
                self.signed_in = True
                cookie = "sna_session=the-session; Path=/api; HttpOnly; Secure; SameSite=lax"
                answer = {"user": {"role": "OWNER"}, "csrfToken": "the-csrf", "step": "DONE"}
                return self._send(request, 200, answer, {"Set-Cookie": cookie})
            if request.path != "/api/health" and not self.signed_in:
                return self._send(request, 403, {"error": {"code": "TWO_FACTOR_REQUIRED", "message": "code first"}})
            if request.path != "/api/health":
                if headers.get("cookie") != "sna_session=the-session":
                    return self._send(request, 401, None)
                if request.command != "GET" and headers.get("x-csrf-token") != "the-csrf":
                    return self._send(request, 403, {"error": {"code": "FORBIDDEN", "message": "no CSRF token"}})
        with self._lock:
            status, answer = self.answer(request.command, request.path, body, headers)
        return self._send(request, status, answer)

    def _send(self, request, status, answer, headers=None):
        payload = b"" if answer is None else json.dumps(answer).encode()
        request.send_response(status)
        for name, value in {"Content-Type": "application/json", **(headers or {})}.items():
            request.send_header(name, value)
        request.send_header("Content-Length", str(len(payload)))
        request.end_headers()
        request.wfile.write(payload)

    def answer(self, method, path, body, headers):
        parts = path.strip("/").split("/")[1:]  # without "api"
        match method, parts:
            case "GET", ["health"]:
                if "degraded" in self.broken:
                    return 503, {"status": "DEGRADED", "database": "DOWN", "broker": "UP"}
                if "broker-down-but-200" in self.broken:  # never from the real back end, but not OK
                    return 200, {"status": "OK", "database": "UP", "broker": "DOWN"}
                return 200, {"status": "OK", "database": "UP", "broker": "UP"}
            case "GET", ["farm"]:
                zones = [{"id": z, "doseLines": ["A", "B", "ACID"]} for z in self.zones]
                return 200, {"site": "siet-ph1", "zones": zones, "features": {"playground": self.playground}}
            case "GET", ["zones"]:
                return 200, {"items": [dict(z) for z in self.zones.values()]}
            case "GET", ["zones", zone]:
                return 200, dict(self.zones[zone])
            case "GET", ["climate" | "power"]:
                return 200, {"online": True}
            case _, ["sim", *rest]:
                return self.sim(method, rest, body)
            case "POST", ["dose-requests"]:
                return self.review(body)
            case "POST", ["dose-requests", review_id, "confirm"]:
                return self.confirm(review_id, headers)
            case "GET", ["commands", cmd_id]:
                return 200, self.step(cmd_id)
            case "POST", ["stop"]:
                return self.stop(body["zone"])
            case "POST", ["resume"]:
                self.zones[body["zone"]]["stopped"] = False
                return 202, self.new_command("RESUME", body["zone"], ["COMPLETED"])
        return 404, {"error": {"code": "NOT_FOUND", "message": f"no {method} {path}"}}

    def sim_state(self):
        return {"scenario": "Demo farm", "speed": self.speed, "faults": list(self.faults), "holds": list(self.holds)}

    def sim(self, method, rest, body):
        match method, rest:
            case "GET", ["state"]:
                pass
            case "POST", ["reset"]:
                self.speed, self.faults, self.holds = START_SPEED, [], []
                self.zones = {z: fresh_zone(z) for z in self.zones}
                if "stays-stopped" in self.broken:
                    self.zones["Z1"]["stopped"] = True
            case "POST", ["speed"]:
                self.speed = body["x"]
            case "POST", ["holds"]:
                self.holds.append(body)
                self.zones[body["target"]]["readings"][body["metric"]] = body["value"]
            case "DELETE", ["holds", zone, metric]:
                self.holds = [h for h in self.holds if (h["target"], h["metric"]) != (zone, metric)]
                # as the simulator: the water keeps its level when released, until Reset
            case "POST", ["holds", "release-all"]:
                self.holds = []
            case "POST", ["faults"]:
                zone, metric = body["target"].split(".")
                stuck = "sensor-stuck" in self.broken and not body["on"]
                if body["on"] or stuck:
                    self.zones[zone]["readings"][metric], self.zones[zone]["quality"][metric] = None, "FAULT"
                else:
                    self.zones[zone]["readings"][metric], self.zones[zone]["quality"][metric] = 6.05, "OK"
                drill = {"kind": body["kind"], "target": body["target"]}
                self.faults = [f for f in self.faults if f != drill] + ([drill] if body["on"] else [])
            case _:
                return 404, {"error": {"code": "NOT_FOUND", "message": "no such path"}}
        return 200, self.sim_state()

    # --- dosing ----------------------------------------------------------------------------

    def review(self, body):
        zone = self.zones[body["zone"]]
        busy = any(c["zone"] == body["zone"] and c["kind"] == "DOSE" and not c["done"] for c in self.commands.values())
        low = zone["readings"]["reservoirPct"] < 30 and "unsafe-allowed" not in self.broken
        unfresh = any(zone["quality"].get(m) != "OK" for m in ("ec", "ph")) and "fresh-not-checked" not in self.broken
        checks = [
            {"id": "STATE_KNOWN", "result": "BLOCK" if busy or zone["stopped"] else "PASS", "message": "state"},
            {"id": "RESERVOIR", "result": "BLOCK" if low else "PASS", "message": "Reservoir 15 % is below 30 %"},
            {"id": "FRESH_READINGS", "result": "BLOCK" if unfresh else "PASS", "message": "pH is FAULT"},
        ]
        review_id = f"dr_{uuid.uuid4().hex[:8]}"
        self.reviews[review_id] = {**body, "checks": checks}
        can = all(c["result"] != "BLOCK" for c in checks)
        return 201, {"id": review_id, "zone": body["zone"], "checks": checks, "canConfirm": can}

    def confirm(self, review_id, headers):
        review = self.reviews[review_id]
        assert "idempotency-key" in headers
        blocked = next((c for c in review["checks"] if c["result"] == "BLOCK"), None)
        if blocked and "confirm-not-refused" not in self.broken:
            return 409, {"error": {"code": blocked["id"], "message": blocked["message"]}}
        ml = review["lines"][0]["ml"]
        ending = "REJECTED" if "rejected" in self.broken else "COMPLETED"
        states = ["ACCEPTED", "EXECUTING", "EXECUTING", "EXECUTING", "EXECUTING", ending, "AFTER"]
        if "timed-out" in self.broken:
            states = ["ACCEPTED", "TIMED_OUT"]
        return 202, self.new_command("DOSE", review["zone"], states, line=review["lines"][0]["line"], ml=ml)

    def new_command(self, kind, zone, states, line=None, ml=0):
        cmd_id = str(uuid.uuid4())
        self.commands[cmd_id] = {"kind": kind, "zone": zone, "states": states, "at": -1, "line": line, "ml": ml,
                                 "done": False, "stopped": False}
        return {"cmdId": cmd_id, "state": "DISPATCHED"}

    def step(self, cmd_id):
        c = self.commands[cmd_id]
        if not c["stopped"]:
            c["at"] = min(c["at"] + 1, len(c["states"]) - 1)
        state = "STOPPED" if c["stopped"] else c["states"][c["at"]]
        progress = (c["at"] + 1) / len(c["states"])
        delivered = c["ml"] if state in ("COMPLETED", "AFTER") else round(c["ml"] * progress / 2)
        after = None
        if state == "AFTER":
            state, after = "COMPLETED", None if "no-after" in self.broken else {"ec": 1.14, "ph": 6.05}
        reason = {"STOPPED": "STOP_REQUESTED", "REJECTED": "ESTOP", "TIMED_OUT": "TIMED_OUT"}.get(state)
        if state == "TIMED_OUT":
            state = "UNKNOWN"
        c["done"] = state in ("REJECTED", "STOPPED") or after is not None or c["kind"] != "DOSE"
        lines = [{"line": c["line"], "requestedMl": c["ml"], "deliveredMl": delivered, "state": "RUNNING"}] if c["line"] else None
        return {"cmdId": cmd_id, "device": "ZC1", "state": state, "reason": reason, "lines": lines,
                "before": {"ec": 1.1, "ph": 6.05} if c["line"] else None, "after": after}

    def stop(self, zone):
        if "stop-ignored" not in self.broken:
            for c in self.commands.values():
                if c["zone"] == zone and c["kind"] == "DOSE" and not c["done"]:
                    c["stopped"] = True
            self.zones[zone]["stopped"] = True
        return 202, self.new_command("STOP", zone, ["COMPLETED"])

    # --- what the script did ---------------------------------------------------------------

    def did(self, method, path=None):
        """The calls with this method, and this exact path if given."""
        return [c for c in self.calls if c["method"] == method and path in (None, c["path"])]


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(demo_check, "POLL_S", 0.005)


@pytest.fixture
def stack():
    fake = FakeStack()
    yield fake
    fake.close()


def run(address, *args, env=None):
    """(exit code, printed lines, what went to stderr)."""
    out, err = io.StringIO(), io.StringIO()
    old_err, sys.stderr = sys.stderr, err
    try:
        code = demo_check.main([address, "--timeout", "3", *args], out=out, env=env or {})
    finally:
        sys.stderr = old_err
    return code, out.getvalue().splitlines(), err.getvalue()


def results(lines):
    """{scenario: (PASS|FAIL|SKIP, detail)} from the printed lines."""
    found = {}
    for line in lines:
        word = line[:5].strip()
        if word in ("PASS", "FAIL", "SKIP"):
            name = next(n for n in [*SCENARIOS, "put back"] if line[6:].startswith(n))
            found[name] = (word, line[6 + len(name):].strip())
    return found


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- a stack that works ----------------------------------------------------------------------


def test_a_working_playground_passes_every_scenario_and_is_put_back(stack):
    code, lines, _ = run(stack.url)
    assert code == 0, lines
    found = results(lines)
    assert list(found) == [*SCENARIOS, "put back"]
    assert {word for word, _ in found.values()} == {"PASS"}
    assert lines[-1] == "8 passed, 0 failed, 0 skipped"
    assert "COMPLETED, A 50 of 50 mL; EC 1.1 -> 1.14, pH 6.05 -> 6.05" in found["dose"][1]
    assert "STOPPED, STOP_REQUESTED" in found["stop and resume"][1]
    assert "RESERVOIR BLOCK" in found["unsafe dose"][1] and "409 RESERVOIR" in found["unsafe dose"][1]
    assert "dose blocked (FRESH_READINGS: pH is FAULT)" in found["sensor fault"][1]
    assert stack.sim_state() == {"scenario": "Demo farm", "speed": START_SPEED, "faults": [], "holds": []}


def test_the_demo_goes_through_the_api_in_order(stack):
    run(stack.url)
    sim_calls = [(c["method"], c["path"]) for c in stack.calls if c["path"].startswith("/api/sim") and c["method"] != "GET"]
    assert sim_calls == [
        ("POST", "/api/sim/reset"),
        ("POST", "/api/sim/holds"), ("DELETE", "/api/sim/holds/Z1/reservoirPct"),
        ("POST", "/api/sim/faults"), ("POST", "/api/sim/faults"),
        ("POST", "/api/sim/holds/release-all"), ("POST", "/api/sim/reset"),
    ]
    holds = stack.did("POST", "/api/sim/holds")[0]["body"]
    assert holds == {"target": "Z1", "metric": "reservoirPct", "value": 15, "gradual": False}
    faults = [c["body"] for c in stack.did("POST", "/api/sim/faults")]
    assert faults == [{"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": True},
                      {"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": False}]
    confirms = [c for c in stack.calls if c["path"].endswith("/confirm")]
    keys = [c["headers"]["idempotency-key"] for c in confirms]
    assert len(keys) == len(set(keys)) == 3 and all(uuid.UUID(k) for k in keys)
    assert [c["body"] for c in stack.did("POST", "/api/stop")] == [{"scope": "ZONE", "zone": "Z1"}]
    assert [c["body"] for c in stack.did("POST", "/api/resume")] == [{"zone": "Z1"}]


def test_the_doses_use_the_amounts_asked_for(stack):
    run(stack.url, "--dose-ml", "40", "--stop-ml", "250", "--low-reservoir-pct", "12")
    amounts = [c["body"]["lines"] for c in stack.did("POST", "/api/dose-requests")]
    assert {"line": "A", "ml": 40} in amounts[0] and [{"line": "A", "ml": 250}] in amounts
    assert stack.did("POST", "/api/sim/holds")[0]["body"]["value"] == 12


def test_another_zone_and_line_can_be_chosen(stack):
    code, lines, _ = run(stack.url, "--zone", "Z2", "--line", "B", "--sensor", "ec")
    assert code == 0, lines
    assert {c["body"]["zone"] for c in stack.did("POST", "/api/dose-requests")} == {"Z2"}
    assert {c["body"]["lines"][0]["line"] for c in stack.did("POST", "/api/dose-requests")} == {"B"}
    assert stack.did("POST", "/api/sim/faults")[0]["body"]["target"] == "Z2.ec"


def test_speed_for_the_check_and_real_speed_for_the_stop(stack):
    run(stack.url, "--speed", "60")
    assert [c["body"]["x"] for c in stack.did("POST", "/api/sim/speed")] == [60, 1, 60]


def test_the_stop_runs_at_real_speed_and_the_speed_is_put_back(stack, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "START_SPEED", 288)  # Reset starts the farm fast
    code, _, _ = run(stack.url)
    assert code == 0
    assert [c["body"]["x"] for c in stack.did("POST", "/api/sim/speed")] == [1, 288]


def test_after_reset_at_real_speed_the_speed_is_not_touched(stack):
    run(stack.url)
    assert stack.did("POST", "/api/sim/speed") == []


def test_a_site_without_the_playground_runs_only_the_checks_that_change_nothing(tmp_path):
    stack = FakeStack(playground=False)
    try:
        code, lines, _ = run(stack.url)
    finally:
        stack.close()
    found = results(lines)
    assert code == 0
    assert [found[n][0] for n in SCENARIOS] == ["PASS", "PASS", "SKIP", "SKIP", "SKIP", "SKIP", "SKIP"]
    assert found["dose"][1] == "not a playground site: the check never doses a real farm (0.0 s)"
    assert "put back" not in found
    assert [c for c in stack.calls if c["method"] != "GET"] == []  # nothing was changed or sent


# --- a stack that doesn't work --------------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "failing", "said"),
    [
        ("degraded", "health", 'GET /api/health answered 503: {"status": "DEGRADED"'),
        ("rejected", "dose", "ended REJECTED (ESTOP)"),
        ("no-after", "dose", "the after reading of"),
        ("timed-out", "dose", "no reply from the farm (UNKNOWN, TIMED_OUT)"),
        ("stop-ignored", "stop and resume", "after Stop, command"),
        ("broker-down-but-200", "health", '"broker": "DOWN"'),
        ("unsafe-allowed", "unsafe dose", "the review did not block the dose"),
        ("confirm-not-refused", "unsafe dose", "confirming the blocked dose answered 202, not 409"),
        ("sensor-stuck", "sensor fault", "Z1 ph back: not seen within"),
        # still blocked, by the low reservoir left from the step before, but not by the fault
        ("fresh-not-checked", "sensor fault", "the review did not block on FRESH_READINGS: RESERVOIR"),
    ],
)
def test_each_failure_is_named_and_the_rest_still_run(broken, failing, said):
    stack = FakeStack(broken=[broken])
    try:
        code, lines, _ = run(stack.url)
        found = results(lines)
        assert code == 1, lines
        assert found[failing][0] == "FAIL", lines
        assert said in found[failing][1], found[failing]
        assert list(found) == [*SCENARIOS, "put back"]  # every scenario reported
        assert found["put back"][0] == "PASS"  # the farm is put back however it went
        assert stack.sim_state()["faults"] == [] and stack.sim_state()["holds"] == []
    finally:
        stack.close()


def test_a_zone_that_stays_stopped_after_reset_fails_reset():
    stack = FakeStack(broken=["stays-stopped"])
    try:
        code, lines, _ = run(stack.url, "--timeout", "0.3")
    finally:
        stack.close()
    assert code == 1
    word, detail = results(lines)["reset"]
    assert word == "FAIL" and detail.startswith("Z1 stopped: True")


def test_a_failing_scenario_leaves_no_drill_or_held_value_behind():
    stack = FakeStack(broken=["unsafe-allowed"])
    try:
        run(stack.url)
        releases = [c["path"] for c in stack.did("DELETE")]
        assert releases == ["/api/sim/holds/Z1/reservoirPct"]  # released straight after the failure
    finally:
        stack.close()


def test_stale_readings_fail_fresh_readings_and_name_the_zone(stack):
    stack.zones["Z2"]["quality"]["ec"] = "STALE"
    code, lines, _ = run(stack.url, "--timeout", "0.2")
    assert code == 1
    assert results(lines)["fresh readings"][0] == "FAIL"
    assert "Z2 ec 1.1 (STALE)" in results(lines)["fresh readings"][1]


def test_an_unknown_zone_is_a_fail_not_a_crash(stack):
    code, lines, _ = run(stack.url, "--zone", "Z9")
    assert code == 1
    assert "there is no zone Z9: the farm has Z1, Z2" in results(lines)["fresh readings"][1]


def test_a_stack_that_is_not_running_fails_fast_and_skips_the_rest():
    code, lines, _ = run(f"http://127.0.0.1:{free_port()}")
    found = results(lines)
    assert code == 1
    assert found["health"][0] == "FAIL" and "cannot reach" in found["health"][1]
    assert [found[n][0] for n in SCENARIOS[2:]] == ["SKIP"] * 5
    assert found["reset"][1].startswith("GET /api/farm could not be read")


def test_a_redirect_is_reported_and_not_followed():
    stack = FakeStack(broken=["redirect"])
    try:
        code, lines, _ = run(stack.url, "--user", "demo", env={"DEMO_CHECK_PASSWORD": "pw"})
    finally:
        stack.close()
    assert code == 1
    assert "was redirected to https://elsewhere.example/api/health: use that address" in results(lines)["health"][1]


# --- the site login -------------------------------------------------------------------------


def test_the_login_is_sent_and_the_password_never_shown():
    password = f"pw-{uuid.uuid4().hex}"
    stack = FakeStack(login=("demo", password))
    try:
        code, lines, err = run(stack.url, "--user", "demo", env={"DEMO_CHECK_PASSWORD": password})
    finally:
        stack.close()
    assert code == 0, lines
    assert password not in "\n".join(lines) + err
    assert all("authorization" in c["headers"] for c in stack.calls)


def test_the_password_variable_can_be_named():
    stack = FakeStack(login=("demo", "secret"))
    try:
        code, _, _ = run(stack.url, "--user", "demo", "--password-env", "STAGING_PW", env={"STAGING_PW": "secret"})
    finally:
        stack.close()
    assert code == 0


def test_a_wrong_password_is_named_as_such():
    stack = FakeStack(login=("demo", "right"))
    try:
        code, lines, _ = run(stack.url, "--user", "demo", env={"DEMO_CHECK_PASSWORD": "wrong"})
    finally:
        stack.close()
    assert code == 1
    assert "401: check --user and --email, and their passwords" in results(lines)["health"][1]


def test_it_signs_in_to_the_app_and_sends_the_cookie_and_the_csrf_token():
    stack = FakeStack(app_login=("owner@example.org", "app-secret"))
    try:
        code, lines, _ = run(stack.url, "--email", "owner@example.org", env={"DEMO_CHECK_APP_PASSWORD": "app-secret"})
    finally:
        stack.close()
    assert code == 0, lines
    assert stack.calls[0]["path"] == "/api/auth/login"
    writes = [call for call in stack.calls[1:] if call["method"] != "GET"]
    assert writes and all(call["headers"].get("x-csrf-token") == "the-csrf" for call in writes)


def test_without_signing_in_to_the_app_every_scenario_after_health_fails():
    stack = FakeStack(app_login=("owner@example.org", "app-secret"))
    try:
        code, lines, _ = run(stack.url)
    finally:
        stack.close()
    assert code == 1
    assert results(lines)["health"][0] == "PASS"
    assert "check --user and --email" in results(lines)["fresh readings"][1]


def test_a_wrong_app_password_does_not_start():
    stack = FakeStack(app_login=("owner@example.org", "right"))
    try:
        code, lines, err = run(stack.url, "--email", "owner@example.org", env={"DEMO_CHECK_APP_PASSWORD": "wrong"})
    finally:
        stack.close()
    assert (code, lines) == (2, [])
    assert "signing in as owner@example.org answered 401: check --email and $DEMO_CHECK_APP_PASSWORD" in err


def test_an_owner_with_two_factor_signs_in_with_the_code_from_the_secret():
    secret = totp.new_secret()
    stack = FakeStack(app_login=("owner@example.org", "app-secret"), totp_secret=secret)
    try:
        env = {"DEMO_CHECK_APP_PASSWORD": "app-secret", "DEMO_CHECK_TOTP_SECRET": secret}
        code, lines, _ = run(stack.url, "--email", "owner@example.org", env=env)
    finally:
        stack.close()
    assert code == 0, lines
    assert [call["path"] for call in stack.calls[:2]] == ["/api/auth/login", "/api/auth/totp"]


def test_an_owner_with_two_factor_but_no_secret_does_not_start():
    stack = FakeStack(app_login=("owner@example.org", "app-secret"), totp_secret=totp.new_secret())
    try:
        code, lines, err = run(stack.url, "--email", "owner@example.org", env={"DEMO_CHECK_APP_PASSWORD": "app-secret"})
    finally:
        stack.close()
    assert (code, lines) == (2, [])
    assert "signs in with an authenticator code: put its secret in $DEMO_CHECK_TOTP_SECRET" in err


def test_its_codes_are_the_back_ends():
    secret = totp.new_secret()
    assert demo_check.totp_code(secret, 1_800_000_000) == totp.code_at(secret, 1_800_000_000 // 30)


def test_an_email_without_a_password_does_not_start(stack):
    code, lines, err = run(stack.url, "--email", "owner@example.org")
    assert (code, lines, stack.calls) == (2, [], [])
    assert "--email owner@example.org needs its password in $DEMO_CHECK_APP_PASSWORD" in err


def test_an_app_password_is_never_sent_unencrypted_to_another_machine():
    code, lines, err = run("http://staging.example.org", "--email", "o@example.org", env={"DEMO_CHECK_APP_PASSWORD": "pw"})
    assert (code, lines) == (2, [])
    assert "use an https:// address" in err


def test_a_user_without_a_password_does_not_start(stack):
    code, lines, err = run(stack.url, "--user", "demo")
    assert (code, lines, stack.calls) == (2, [], [])
    assert "--user demo needs its password in $DEMO_CHECK_PASSWORD" in err


@pytest.mark.parametrize(
    "address", ["http://staging.example.org", "http://192.168.1.20:8000"],
)
def test_a_password_is_never_sent_unencrypted_to_another_machine(address):
    code, lines, err = run(address, "--user", "demo", env={"DEMO_CHECK_PASSWORD": "pw"})
    assert (code, lines) == (2, [])
    assert "use an https:// address" in err


def test_this_machine_may_take_a_password_over_http():
    code, lines, _ = run(f"http://127.0.0.1:{free_port()}", "--user", "demo", "--timeout", "0.1",
                         env={"DEMO_CHECK_PASSWORD": "pw"})
    assert code == 1 and "cannot reach" in results(lines)["health"][1]  # it tried


@pytest.mark.parametrize(
    ("host", "local"),
    [("localhost", True), ("127.0.0.1", True), ("127.8.9.10", True), ("::1", True),
     ("staging.example.org", False), ("10.0.0.5", False), ("::2", False)],
)
def test_what_counts_as_this_machine(host, local):
    assert demo_check._this_machine(host) is local


@pytest.mark.parametrize("address", ["ftp://example.org", "localhost:8000", "http://"])
def test_an_address_that_isnt_one_does_not_start(address):
    code, lines, err = run(address)
    assert (code, lines) == (2, [])
    assert "is not an http:// or https:// address" in err


def test_insecure_says_so(stack):
    code, _, err = run(stack.url, "--insecure")
    assert code == 0
    assert "--insecure: the TLS certificate is not checked" in err


def test_it_runs_as_a_program_with_only_the_standard_library():
    done = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), f"http://127.0.0.1:{free_port()}", "--timeout", "0.1"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 1, done.stderr
    assert done.stdout.splitlines()[-1] == "0 passed, 2 failed, 5 skipped"
