"""Demo check (T1-23): run the demo scenarios against a running stack, through its API only.

    DEMO_CHECK_APP_PASSWORD=... python scripts/demo-check.py http://localhost:8000 --email owner@example.org
    DEMO_CHECK_PASSWORD=... DEMO_CHECK_APP_PASSWORD=... \
        python scripts/demo-check.py https://staging.example.org --user demo --email owner@example.org

Run it after deploying, and again before the demo. It prints PASS, FAIL or SKIP for each scenario,
and exits with 0 when none failed, 1 when one did, and 2 when it could not start.

    health           GET /api/health answers OK, with the database and the broker UP
    fresh readings   every zone online with fresh EC and pH; the climate controller and the power
                     monitor online
    reset            Reset: no drills, no held values, no zone stopped, fresh readings again
    dose             a dose request, confirmed, followed to COMPLETED with before and after
    stop and resume  a dose stopped mid-way (STOPPED, STOP_REQUESTED), then Resume
    unsafe dose      the reservoir held at 15 %: the review blocks the dose and confirm is refused
    sensor fault     a probe offline: its reading turns FAULT and doses are blocked; Fix restores it

Everything after "fresh readings" uses the playground (/api/sim), so it runs only on a site whose
features.playground is true. On any other site it is skipped: the check never doses a real farm.
At the end it puts the demo farm back as it starts: values released, drills fixed, and Reset.

The site login (the staging site's password) is given as --user, with the password in an
environment variable (DEMO_CHECK_PASSWORD unless --password-env says otherwise), never on the
command line or in a file. The app's own sign-in (T1-35) is --email, with its password in
DEMO_CHECK_APP_PASSWORD (or --app-password-env): every API call but /api/health needs it. Use an
Owner, because the check resets the simulated farm. An Owner signs in with an authenticator code
too: the check makes it from the Owner's two-factor secret in DEMO_CHECK_TOTP_SECRET (or
--totp-env), the secret the app showed when two-factor was set up. Passwords are sent only over
https, or to this machine.

Only the standard library, so any Python 3.11 or later runs it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import ssl
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any, TextIO
from urllib.parse import quote, urlsplit

POLL_S = 1.0  # between two looks while waiting for the farm
REQUEST_TIMEOUT_S = 20.0
FINAL = ("COMPLETED", "PARTIAL", "FAILED", "STOPPED", "REJECTED", "EXPIRED")  # contract 8.1
PASSWORD_ENV = "DEMO_CHECK_PASSWORD"
APP_PASSWORD_ENV = "DEMO_CHECK_APP_PASSWORD"
TOTP_ENV = "DEMO_CHECK_TOTP_SECRET"
SESSION_COOKIE = "sna_session"  # the back end's session cookie (backend/app/auth/sessions.py)
SENSOR_DRILL = "SENSOR_OFFLINE"  # the probe-offline drill (scenarios.json, contract 11.4.3)


class Fail(Exception):
    """A scenario did not go as the demo needs; the text says what was seen."""


class CannotStart(Exception):
    """Bad arguments or settings: nothing was checked."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # never carry the login to another address


class Api:
    """The stack's API at one address, with the site login if there is one."""

    def __init__(self, address: str, user: str | None, password: str | None, insecure: bool,
                 signs_in: bool = False):
        parts = urlsplit(address)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise CannotStart(f"{address!r} is not an http:// or https:// address")
        if (user is not None or signs_in) and parts.scheme == "http" and not _this_machine(parts.hostname):
            raise CannotStart("the password would travel unencrypted: use an https:// address")
        self.base = address.rstrip("/")
        self._headers = {"Accept": "application/json"}
        self._csrf: str | None = None
        if user is not None:
            token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            self._headers["Authorization"] = f"Basic {token}"
        context = ssl.create_default_context()
        if insecure:
            context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _NoRedirects())

    def sign_in(self, email: str, password: str, totp_secret: str | None = None) -> None:
        """POST /api/auth/login, and the authenticator code when it asks for one, then send the
        session cookie on every call and the CSRF token on every call that isn't a GET (contract
        section 11.1). CannotStart if it is refused."""
        status, answer, cookies = self._send("POST", "/api/auth/login", {"email": email, "password": password}, {})
        if status != 200 or not isinstance(answer, dict) or SESSION_COOKIE not in cookies:
            why = f"check --email and ${APP_PASSWORD_ENV}" if status == 401 else _short(answer)
            raise CannotStart(f"signing in as {email} answered {status}: {why}")
        self._headers["Cookie"] = f"{SESSION_COOKIE}={cookies[SESSION_COOKIE]}"
        self._csrf = answer.get("csrfToken")
        if answer.get("step") == "TOTP_SETUP":
            raise CannotStart(f"{email} has no two-factor yet: set it up by signing in to the app first")
        if answer.get("step") == "TOTP":
            if not totp_secret:
                raise CannotStart(f"{email} signs in with an authenticator code: put its secret in ${TOTP_ENV}")
            status, answer, cookies = self._send("POST", "/api/auth/totp", {"code": totp_code(totp_secret)}, {})
            if status != 200 or not isinstance(answer, dict) or SESSION_COOKIE not in cookies:
                raise CannotStart(f"the authenticator code for {email} answered {status}: check ${TOTP_ENV}")
            self._headers["Cookie"] = f"{SESSION_COOKIE}={cookies[SESSION_COOKIE]}"
            self._csrf = answer.get("csrfToken")

    def call(self, method: str, path: str, body: Any = None, headers: Mapping[str, str] | None = None) -> tuple[int, Any]:
        """(status, JSON answer or None), whatever the status."""
        status, answer, _ = self._send(method, path, body, headers or {})
        if status == 401:
            raise Fail(f"{method} {path} answered 401: check --user and --email, and their passwords")
        return status, answer

    def _send(self, method: str, path: str, body: Any, headers: Mapping[str, str]) -> tuple[int, Any, dict[str, str]]:
        """(status, JSON answer or None, the cookies it set), whatever the status."""
        data = None if body is None else json.dumps(body).encode()
        csrf = {"X-CSRF-Token": self._csrf} if self._csrf and method != "GET" else {}
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={
            **self._headers, **csrf, **({"Content-Type": "application/json"} if data is not None else {}), **headers,
        })
        try:
            try:
                with self._opener.open(request, timeout=REQUEST_TIMEOUT_S) as response:
                    status, raw, set_cookies = response.status, response.read(), response.headers.get_all("Set-Cookie")
            except urllib.error.HTTPError as answer:
                with answer:
                    status, raw, set_cookies = answer.code, answer.read(), answer.headers.get_all("Set-Cookie")
                if 300 <= status < 400:
                    raise Fail(f"{method} {path} was redirected to {answer.headers.get('Location')}: use that address")
        except urllib.error.URLError as exc:
            raise Fail(f"{method} {path}: cannot reach {self.base} ({exc.reason})") from exc
        except (OSError, ValueError) as exc:
            raise Fail(f"{method} {path}: {exc}") from exc
        cookies = {}
        for line in set_cookies or []:
            name, _, value = line.split(";", 1)[0].partition("=")
            cookies[name.strip()] = value.strip()
        try:
            return status, json.loads(raw) if raw.strip() else None, cookies
        except ValueError as exc:
            raise Fail(f"{method} {path} answered {status} with something that is not JSON") from exc

    def get(self, path: str) -> Any:
        return self.expect("GET", path, 200)

    def expect(self, method: str, path: str, status: int, body: Any = None, headers: Mapping[str, str] | None = None) -> Any:
        got, answer = self.call(method, path, body, headers)
        if got != status:
            raise Fail(f"{method} {path} answered {got}, not {status}: {_short(answer)}")
        return answer


def totp_code(secret: str, now: float | None = None) -> str:
    """The authenticator app's current code (RFC 6238: SHA-1, 6 digits, 30 s), as the back end checks it."""
    key = base64.b32decode(secret.replace(" ", "").upper())
    step = int(time.time() if now is None else now) // 30
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**6).zfill(6)


def _this_machine(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _short(value: Any, limit: int = 300) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def wait_for(what: str, look: Callable[[], Any], timeout_s: float) -> Any:
    """look() until it returns something true; Fail with the last thing seen after timeout_s."""
    deadline = time.monotonic() + timeout_s
    while True:
        seen = look()
        if seen:
            return seen
        if time.monotonic() >= deadline:
            raise Fail(f"{what}: not seen within {timeout_s:g} s")
        time.sleep(POLL_S)


class Check:
    """The scenarios, in order, against one stack."""

    def __init__(self, api: Api, options: argparse.Namespace, out: TextIO):
        self.api, self.options, self.out = api, options, out
        self.timeout_s: float = options.timeout
        self.results: list[tuple[str, str, str]] = []
        self.playground: bool | None = None  # None: the farm could not be read
        self.zone = self.line = ""
        self.touched = False  # the playground was changed, so it is put back at the end

    # --- running -------------------------------------------------------------------------

    def run(self) -> int:
        self._say(f"Demo check: {self.api.base}")
        self._scenario("health", self.health)
        self._scenario("fresh readings", self.fresh_readings)
        for name, scenario in (
            ("reset", self.reset),
            ("dose", self.dose),
            ("stop and resume", self.stop_and_resume),
            ("unsafe dose", self.unsafe_dose),
            ("sensor fault", self.sensor_fault),
        ):
            if self.playground:
                self._scenario(name, scenario)
            elif self.playground is None:
                self._record("SKIP", name, "GET /api/farm could not be read", 0.0)
            else:
                self._record("SKIP", name, "not a playground site: the check never doses a real farm", 0.0)
        if self.touched:
            self._scenario("put back", self.put_back)
        counts = {word: sum(1 for result, _, _ in self.results if result == word) for word in ("PASS", "FAIL", "SKIP")}
        self._say(f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
        return 1 if counts["FAIL"] else 0

    def _scenario(self, name: str, scenario: Callable[[], str]) -> None:
        started = time.monotonic()
        try:
            detail, result = scenario(), "PASS"
        except Fail as exc:
            detail, result = str(exc), "FAIL"
        except Exception as exc:  # noqa: BLE001 - a bug in one scenario still lets the others run
            detail, result = f"unexpected {type(exc).__name__}: {exc}", "FAIL"
        self._record(result, name, detail, time.monotonic() - started)

    def _record(self, result: str, name: str, detail: str, seconds: float) -> None:
        self.results.append((result, name, detail))
        self._say(f"{result:<5} {name:<16} {detail} ({seconds:.1f} s)")

    def _say(self, line: str) -> None:
        print(line, file=self.out, flush=True)

    # --- what the farm says ----------------------------------------------------------------

    def _zone(self) -> dict[str, Any]:
        return self.api.get(f"/api/zones/{self.zone}")

    def _review(self, ml: float) -> dict[str, Any]:
        body = {"zone": self.zone, "lines": [{"line": self.line, "ml": ml}]}
        return self.api.expect("POST", "/api/dose-requests", 201, body)

    def _confirmable(self, ml: float) -> dict[str, Any]:
        """A review that may be confirmed, once the zone has settled (fresh readings, no operation)."""
        last: dict[str, Any] = {}

        def look() -> dict[str, Any] | None:
            last.update(self._review(ml))
            return dict(last) if last.get("canConfirm") else None

        try:
            return wait_for("a dose that may be confirmed", look, self.timeout_s)
        except Fail:
            raise Fail(f"the review kept blocking: {_blocking(last)}") from None

    def _confirm(self, review: dict[str, Any]) -> str:
        path = f"/api/dose-requests/{review['id']}/confirm"
        sent = self.api.expect("POST", path, 202, headers={"Idempotency-Key": str(uuid.uuid4())})
        return sent["cmdId"]

    def _command(self, cmd_id: str) -> dict[str, Any]:
        command = self.api.get(f"/api/commands/{cmd_id}")
        if command["state"] == "UNKNOWN" and command.get("reason") == "TIMED_OUT":
            raise Fail(f"command {cmd_id}: no reply from the farm (UNKNOWN, TIMED_OUT)")
        return command

    def _ended(self, cmd_id: str) -> dict[str, Any]:
        return wait_for(f"command {cmd_id} to end", lambda: (c := self._command(cmd_id))["state"] in FINAL and c, self.timeout_s)

    def _speed(self, x: float) -> None:
        self.api.expect("POST", "/api/sim/speed", 200, {"x": x})

    # --- the scenarios -------------------------------------------------------------------

    def health(self) -> str:
        status, answer = self.api.call("GET", "/api/health")
        healthy = {"status": "OK", "database": "UP", "broker": "UP"}
        if status != 200 or not isinstance(answer, dict) or any(answer.get(k) != v for k, v in healthy.items()):
            raise Fail(f"GET /api/health answered {status}: {_short(answer)}")
        return f"OK: database {answer.get('database')}, broker {answer.get('broker')}"

    def fresh_readings(self) -> str:
        farm = self.api.get("/api/farm")
        self.playground = farm.get("features", {}).get("playground") is True
        zones = [zone["id"] for zone in farm["zones"]]
        if not zones:
            raise Fail("GET /api/farm lists no zones")
        chosen = self.options.zone or zones[0]
        if chosen not in zones:
            raise Fail(f"there is no zone {chosen}: the farm has {', '.join(zones)}")
        self.zone = chosen
        lines = next(zone["doseLines"] for zone in farm["zones"] if zone["id"] == chosen)
        self.line = self.options.line or lines[0]
        problems: list[str] = []

        def look() -> bool:
            problems.clear()
            for zone in self.api.get("/api/zones")["items"]:
                problems.extend(_unfresh(zone))
            for name, path in (("climate", "/api/climate"), ("power", "/api/power")):
                if self.api.get(path).get("online") is not True:
                    problems.append(f"{name} offline")
            return not problems

        try:
            wait_for("fresh readings", look, min(self.timeout_s, 120))
        except Fail:
            raise Fail("; ".join(problems)) from None
        kind = "a playground site" if self.playground else "not a playground site"
        return f"{', '.join(zones)} online with fresh EC and pH; climate and power online ({kind})"

    def reset(self) -> str:
        self.touched = True
        state = self.api.expect("POST", "/api/sim/reset", 200)
        if state.get("faults") or state.get("holds"):
            raise Fail(f"after Reset: {_short(state)}")
        problems: list[str] = []

        def look() -> bool:
            problems.clear()
            for zone in self.api.get("/api/zones")["items"]:
                problems.extend(_unfresh(zone))
                if zone.get("stopped") is not False:
                    problems.append(f"{zone['zone']} stopped: {zone.get('stopped')}")
            return not problems

        try:
            wait_for("the farm at its start", look, self.timeout_s)
        except Fail:
            raise Fail("; ".join(problems)) from None
        speed = state.get("speed")
        if self.options.speed is not None:
            self._speed(self.options.speed)
            speed = f"{state.get('speed')}, now x{self.options.speed:g} for the check"
        return f"no drills, no held values, every zone running with fresh readings (speed {speed})"

    def dose(self) -> str:
        review = self._confirmable(self.options.dose_ml)
        cmd_id = self._confirm(review)
        ended = self._ended(cmd_id)
        if ended["state"] != "COMPLETED":
            raise Fail(f"command {cmd_id} ended {ended['state']} ({ended.get('reason')})")
        command = wait_for(
            f"the after reading of {cmd_id} (the mix wait)",
            lambda: (c := self._command(cmd_id))["after"] is not None and c,
            self.timeout_s,
        )
        before, after = command.get("before") or {}, command["after"]
        lines = ", ".join(f"{ln['line']} {ln['deliveredMl']:g} of {ln['requestedMl']:g} mL" for ln in command["lines"])
        return (f"{cmd_id}: COMPLETED, {lines}; EC {before.get('ec')} -> {after.get('ec')}, "
                f"pH {before.get('ph')} -> {after.get('ph')}")

    def stop_and_resume(self) -> str:
        speed = self.api.get("/api/sim/state").get("speed")
        if isinstance(speed, (int, float)) and speed != 1:
            self._speed(1)  # at real speed a dose runs long enough to be stopped mid-way
        try:
            cmd_id = self._confirm(self._confirmable(self.options.stop_ml))
            wait_for(
                f"command {cmd_id} to be dosing",
                lambda: (c := self._command(cmd_id))["state"] == "EXECUTING"
                and any(ln["deliveredMl"] > 0 for ln in c.get("lines") or []),
                self.timeout_s,
            )
            self.api.expect("POST", "/api/stop", 202, {"scope": "ZONE", "zone": self.zone})
            ended = self._ended(cmd_id)
            if (ended["state"], ended.get("reason")) != ("STOPPED", "STOP_REQUESTED"):
                raise Fail(f"after Stop, command {cmd_id} ended {ended['state']} ({ended.get('reason')})")
            delivered = sum(ln["deliveredMl"] for ln in ended["lines"])
            wait_for(f"{self.zone} stopped", lambda: self._zone().get("stopped") is True, self.timeout_s)
            resume_id = self.api.expect("POST", "/api/resume", 202, {"zone": self.zone})["cmdId"]
            resumed = self._ended(resume_id)
            if resumed["state"] != "COMPLETED":
                raise Fail(f"Resume {resume_id} ended {resumed['state']} ({resumed.get('reason')})")
            wait_for(f"{self.zone} running again", lambda: self._zone().get("stopped") is False, self.timeout_s)
        finally:
            if isinstance(speed, (int, float)) and speed != 1:
                self.api.call("POST", "/api/sim/speed", {"x": speed})  # as it was; Reset also restores
        return (f"{cmd_id}: STOPPED, STOP_REQUESTED after {delivered:g} of {self.options.stop_ml:g} mL; "
                f"Resume COMPLETED, {self.zone} running again")

    def unsafe_dose(self) -> str:
        pct = self.options.low_reservoir_pct
        self.api.expect("POST", "/api/sim/holds", 200,
                        {"target": self.zone, "metric": "reservoirPct", "value": pct, "gradual": False})
        try:
            wait_for(f"{self.zone} reservoir at {pct:g} %",
                     lambda: _near(self._zone()["readings"].get("reservoirPct"), pct), self.timeout_s)
            review = self._review(self.options.dose_ml)
            reservoir = next((c for c in review["checks"] if c["id"] == "RESERVOIR"), None)
            if review.get("canConfirm") is not False or reservoir is None or reservoir["result"] != "BLOCK":
                raise Fail(f"the review did not block the dose: canConfirm {review.get('canConfirm')}, "
                           f"RESERVOIR {reservoir and reservoir['result']}")
            status, refused = self.api.call("POST", f"/api/dose-requests/{review['id']}/confirm",
                                            headers={"Idempotency-Key": str(uuid.uuid4())})
            code = (refused or {}).get("error", {}).get("code") if isinstance(refused, dict) else None
            if status != 409:
                raise Fail(f"confirming the blocked dose answered {status}, not 409: {_short(refused)}")
        finally:
            self.api.call("DELETE", f"/api/sim/holds/{quote(self.zone, safe='')}/reservoirPct")
        return f"reservoir {pct:g} %: RESERVOIR BLOCK ({reservoir['message']}); confirm refused, 409 {code}"

    def sensor_fault(self) -> str:
        sensor = self.options.sensor
        drill = {"kind": SENSOR_DRILL, "target": f"{self.zone}.{sensor}"}
        self.api.expect("POST", "/api/sim/faults", 200, {**drill, "on": True})
        try:
            wait_for(f"{self.zone} {sensor} FAULT", lambda: _quality(self._zone(), sensor) == ("FAULT", True), self.timeout_s)
            review = self._review(self.options.dose_ml)
            fresh = next((c for c in review["checks"] if c["id"] == "FRESH_READINGS"), None)
            if review.get("canConfirm") is not False or fresh is None or fresh["result"] != "BLOCK":
                raise Fail(f"with {sensor} FAULT the review did not block on FRESH_READINGS: {_blocking(review)}")
            blocked = f"FRESH_READINGS: {fresh['message']}"
        finally:
            fixed, answer = self.api.call("POST", "/api/sim/faults", {**drill, "on": False})
        if fixed != 200:
            raise Fail(f"Fix answered {fixed}: {_short(answer)}")
        wait_for(f"{self.zone} {sensor} back", lambda: _quality(self._zone(), sensor) == ("OK", False), self.timeout_s)
        return f"{SENSOR_DRILL} on {self.zone}.{sensor}: null, FAULT, dose blocked ({blocked}); Fix: OK again"

    def put_back(self) -> str:
        self.api.call("POST", "/api/sim/holds/release-all")
        state = self.api.expect("POST", "/api/sim/reset", 200)
        return f"values released and Reset: speed {state.get('speed')}, no drills, no held values"


def _unfresh(zone: dict[str, Any]) -> list[str]:
    name, quality, readings = zone.get("zone"), zone.get("quality") or {}, zone.get("readings") or {}
    if zone.get("online") is not True:
        return [f"{name} offline"]
    return [f"{name} {metric} {readings.get(metric)} ({quality.get(metric)})"
            for metric in ("ec", "ph") if readings.get(metric) is None or quality.get(metric) != "OK"]


def _quality(zone: dict[str, Any], metric: str) -> tuple[str | None, bool]:
    return (zone.get("quality") or {}).get(metric), (zone.get("readings") or {}).get(metric) is None


def _near(value: Any, target: float) -> bool:
    return isinstance(value, (int, float)) and abs(value - target) <= 0.5


def _blocking(review: dict[str, Any]) -> str:
    blocks = [f"{c['id']}: {c['message']}" for c in review.get("checks", []) if c.get("result") == "BLOCK"]
    return "; ".join(blocks) or "nothing blocking"


def _options(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="demo-check.py", description="Run the demo scenarios against a running stack.")
    parser.add_argument("address", help="the stack's address, for example http://localhost:8000")
    parser.add_argument("--user", help=f"the site login; its password comes from ${PASSWORD_ENV}")
    parser.add_argument("--password-env", default=PASSWORD_ENV, metavar="NAME",
                        help=f"the environment variable holding the password (default {PASSWORD_ENV})")
    parser.add_argument("--email", help=f"sign in to the app as this user, an Owner; its password comes from ${APP_PASSWORD_ENV}")
    parser.add_argument("--app-password-env", default=APP_PASSWORD_ENV, metavar="NAME",
                        help=f"the environment variable holding the app password (default {APP_PASSWORD_ENV})")
    parser.add_argument("--totp-env", default=TOTP_ENV, metavar="NAME",
                        help=f"the environment variable holding the Owner's two-factor secret (default {TOTP_ENV})")
    parser.add_argument("--zone", help="the zone to dose (default: the farm's first)")
    parser.add_argument("--line", help="the dose line to use (default: the zone's first)")
    parser.add_argument("--dose-ml", type=float, default=50, help="mL for the dose scenario (default 50)")
    parser.add_argument("--stop-ml", type=float, default=200, help="mL for the dose that is stopped (default 200)")
    parser.add_argument("--low-reservoir-pct", type=float, default=15, help="the unsafe reservoir level (default 15)")
    parser.add_argument("--sensor", default="ph", help="the probe the sensor fault takes offline (default ph)")
    parser.add_argument("--speed", type=float, help="the simulator speed for the check, for example 60 (Reset puts the start speed back)")
    parser.add_argument("--timeout", type=float, default=420, help="seconds any one wait may take (default 420: a 300 s mix wait at x1)")
    parser.add_argument("--insecure", action="store_true", help="don't check the TLS certificate (a local staging test only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout, env: Mapping[str, str] = os.environ) -> int:
    options = _options(sys.argv[1:] if argv is None else argv)
    password = env.get(options.password_env) if options.user is not None else None
    app_password = env.get(options.app_password_env) if options.email is not None else None
    try:
        if options.user is not None and not password:
            raise CannotStart(f"--user {options.user} needs its password in ${options.password_env}")
        if options.email is not None and not app_password:
            raise CannotStart(f"--email {options.email} needs its password in ${options.app_password_env}")
        api = Api(options.address, options.user, password, options.insecure, signs_in=options.email is not None)
        if options.email is not None:
            try:
                api.sign_in(options.email, app_password, env.get(options.totp_env))
            except Fail as exc:
                raise CannotStart(str(exc)) from exc
    except CannotStart as exc:
        print(f"demo-check: {exc}", file=sys.stderr)
        return 2
    if options.insecure:
        print("demo-check: --insecure: the TLS certificate is not checked", file=sys.stderr)
    return Check(api, options, out).run()


if __name__ == "__main__":
    sys.exit(main())
