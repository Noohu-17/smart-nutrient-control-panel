"""The simulator's own control API (contract 11.4): a small HTTP server for the back end only.

It listens on the internal network (SIM_CONTROL_HOST, 127.0.0.1 unless set) and is never exposed:
people reach it through the back end's /api/sim/..., which checks their role and writes the audit
log. JSON in and out, errors as contract 11.1. Each change answers with the new state, as
GET /state would.

    GET    /state                     scenario, simulated time, speed, weather, drills, holds
    POST   /speed                     {"x": 60}: one of simulator.json speedOptions; 0 pauses
    POST   /reset                     back to the demo starting state
    POST   /holds                     {"target": "Z1", "metric": "ph", "value": 7.2}; "gradual" too
    DELETE /holds/{target}/{metric}   release one held value
    POST   /holds/release-all         release them all
    POST   /faults                    {"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": true}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from typing import Any
from urllib.parse import unquote, urlsplit

from .playground import Playground, PlaygroundError

log = logging.getLogger("sim.control")

MAX_BODY_BYTES = 64 * 1024
READ_TIMEOUT_S = 10.0

Action = Callable[[Playground, Any, tuple[str, ...]], Any]


def _release(playground: Playground, _body: Any, params: tuple[str, ...]) -> None:
    target, metric = params
    if not playground.release(target, metric):
        raise PlaygroundError(404, "NOT_FOUND", f"Nothing is held on {target} {metric}")


ROUTES: dict[tuple[str, ...], dict[str, Action]] = {  # path, with "*" for a parameter
    ("state",): {"GET": lambda p, body, params: None},
    ("speed",): {"POST": lambda p, body, params: p.set_speed(body)},
    ("reset",): {"POST": lambda p, body, params: p.reset()},
    ("holds",): {"POST": lambda p, body, params: p.hold(body)},
    ("holds", "release-all"): {"POST": lambda p, body, params: p.release_all()},
    ("holds", "*", "*"): {"DELETE": _release},
    ("faults",): {"POST": lambda p, body, params: p.fault(body)},
}


async def serve(playground: Playground, host: str, port: int) -> asyncio.Server:
    """Start listening; port 0 picks a free one (tests)."""
    return await asyncio.start_server(partial(_handle, playground), host, port)


async def run_control_api(playground: Playground, host: str, port: int) -> None:
    server = await serve(playground, host, port)
    log.info("playground control API on http://%s:%d (contract 11.4)", host, port)
    async with server:
        await server.serve_forever()


def answer(
    playground: Playground, method: str, path: str, body: bytes
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """(status, JSON body, extra headers) for one request."""
    segments = tuple(unquote(part) for part in path.strip("/").split("/") if part)
    route, params = _route(segments)
    if route is None:
        return _error(PlaygroundError(404, "NOT_FOUND", f"No such path: {path}"))
    action = route.get(method)
    if action is None:
        allow = ", ".join(route)
        error = PlaygroundError(405, "METHOD_NOT_ALLOWED", f"{path} takes {allow}")
        return (*_error(error)[:2], {"Allow": allow})
    try:
        action(playground, _json(body) if method == "POST" else None, params)
    except PlaygroundError as error:
        return _error(error)
    return 200, playground.state(), {}


def _route(segments: tuple[str, ...]) -> tuple[dict[str, Action] | None, tuple[str, ...]]:
    if segments in ROUTES:  # a fixed path wins over a parameter: /holds/release-all
        return ROUTES[segments], ()
    for pattern, route in ROUTES.items():
        if len(pattern) == len(segments) and all(
            p == "*" or p == s for p, s in zip(pattern, segments, strict=True)
        ):
            return route, tuple(s for p, s in zip(pattern, segments, strict=True) if p == "*")
    return None, ()


def _json(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise PlaygroundError(422, "VALIDATION_ERROR", "The body must be JSON", "body") from None


def _error(error: PlaygroundError) -> tuple[int, dict[str, Any], dict[str, str]]:
    return error.status, error.body(), {}


async def _handle(
    playground: Playground, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """One request per connection (Connection: close)."""
    try:
        try:
            method, path, body = await asyncio.wait_for(_read_request(reader), READ_TIMEOUT_S)
        except PlaygroundError as error:
            status, payload, headers = _error(error)
            method = path = "?"
        else:
            try:
                status, payload, headers = answer(playground, method, path, body)
            except Exception:
                log.exception("control API: %s %s failed", method, path)
                error = PlaygroundError(500, "INTERNAL", "Something went wrong in the simulator")
                status, payload, headers = _error(error)
        if method != "GET" or status != 200:
            log.info("control API: %s %s -> %d", method, path, status)
        writer.write(_response(status, payload, headers))
        await writer.drain()
    except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
        pass  # the caller went away or never finished its request: nothing to answer
    finally:
        writer.close()


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, bytes]:
    request_line = (await reader.readline()).decode("latin-1").split()
    if len(request_line) != 3:
        raise PlaygroundError(400, "VALIDATION_ERROR", "Not an HTTP request")
    method, target, _version = request_line
    length = 0
    while (line := await reader.readline()) not in (b"\r\n", b"\n", b""):
        name, _, value = line.decode("latin-1").partition(":")
        if name.strip().lower() == "content-length":
            try:
                length = int(value.strip())
            except ValueError:
                raise PlaygroundError(400, "VALIDATION_ERROR", "Bad Content-Length") from None
    if length > MAX_BODY_BYTES:
        raise PlaygroundError(413, "VALIDATION_ERROR", f"The body is over {MAX_BODY_BYTES} bytes")
    body = await reader.readexactly(length) if length > 0 else b""
    return method.upper(), urlsplit(target).path, body


def _response(status: int, payload: dict[str, Any], headers: dict[str, str]) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    lines = [
        f"HTTP/1.1 {status} {HTTPStatus(status).phrase}",
        "Content-Type: application/json",
        f"Content-Length: {len(data)}",
        "Connection: close",
        *(f"{name}: {value}" for name, value in headers.items()),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + data
