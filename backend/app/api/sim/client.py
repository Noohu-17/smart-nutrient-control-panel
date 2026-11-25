"""Calling the simulator's own control API (T4-9) over HTTP, from inside the server.

Standard library only. Proxies set in the environment are ignored and redirects are never
followed, so a call can only reach the address in SIM_CONTROL_URL. Nothing from the app's request
(cookies, headers) is passed on: only the method, the path and the JSON body the route built.
"""

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

TIMEOUT_S = 5.0  # the simulator keeps its farm in memory, so it answers at once or not at all
MAX_ANSWER_BYTES = 1_000_000


class SimulatorDown(Exception):
    """The simulator couldn't be reached, so the call never got to it."""


class SimulatorTimedOut(Exception):
    """The call was sent, but no answer came in time: the change may or may not have been made."""


class BadAnswer(Exception):
    """The simulator answered something the API can't pass on."""


@dataclass(frozen=True)
class SimAnswer:
    status: int
    body: Any  # the JSON it sent, or None for an empty answer


class Simulator(Protocol):
    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> SimAnswer:
        """Send one call and return the simulator's answer, whatever its status."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None  # a 3xx then comes back as the simulator's answer, which isn't passed on


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")  # NaN and Infinity, which Python's json would accept


def _parse(raw: bytes) -> Any:
    if len(raw) > MAX_ANSWER_BYTES:
        raise BadAnswer(f"the answer is over {MAX_ANSWER_BYTES} bytes")
    if not raw.strip():
        return None
    try:
        return json.loads(raw, parse_constant=_no_constants)
    except ValueError as exc:  # also UnicodeDecodeError
        raise BadAnswer(f"the answer is not JSON: {exc}") from exc


class HttpSimulator:
    def __init__(self, base_url: str, *, timeout_s: float = TIMEOUT_S):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise ValueError(
                "SIM_CONTROL_URL must be an http:// or https:// address, such as http://simulator:8100"
            )
        self._base = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> SimAnswer:
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
        try:
            try:
                with opener.open(request, timeout=self._timeout_s) as response:
                    status, raw = response.status, response.read(MAX_ANSWER_BYTES + 1)
            except urllib.error.HTTPError as answer:  # any status but 2xx: still the simulator's answer
                with answer:
                    status, raw = answer.code, answer.read(MAX_ANSWER_BYTES + 1)
        except urllib.error.URLError as exc:  # before the call was sent: refused, unreachable, bad host
            raise SimulatorDown(str(exc.reason)) from exc
        except (OSError, http.client.HTTPException) as exc:  # after it was sent: timed out, or cut off
            raise SimulatorTimedOut(str(exc) or type(exc).__name__) from exc
        return SimAnswer(status, _parse(raw))
