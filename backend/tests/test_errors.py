"""Every error leaves the API in the contract's shape (§11.1), whichever part of FastAPI raised it."""

import asyncio
import json
from typing import Any

from fastapi import FastAPI, HTTPException

from app.errors import add_error_handlers
from app.main import app as real_app


def call(app: FastAPI, method: str, path: str, query: str = "") -> tuple[int, dict[str, str], Any]:
    """One request through the whole app, as uvicorn would make it."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "scheme": "https",
        "query_string": query.encode(),
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 443),
    }
    try:
        asyncio.run(app(scope, receive, send))
    except RuntimeError as exc:
        # After answering a 500, the server layer raises the error again so that it gets logged.
        assert str(exc) == "secret detail"

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {name.decode(): value.decode() for name, value in start["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], headers, json.loads(body)


def app_with_routes() -> FastAPI:
    app = FastAPI()
    add_error_handlers(app)

    @app.get("/conflict")
    async def conflict() -> None:
        raise HTTPException(status_code=409, detail="Another dose is running")

    @app.get("/number")
    async def number(n: int) -> int:
        return n

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret detail")

    return app


def test_the_real_app_answers_an_unknown_path_in_the_contract_shape() -> None:
    status, _, body = call(real_app, "GET", "/api/nope")
    assert status == 404
    assert body == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


def test_an_http_exception_keeps_its_status_and_message() -> None:
    status, _, body = call(app_with_routes(), "GET", "/conflict")
    assert status == 409
    assert body == {"error": {"code": "CONFLICT", "message": "Another dose is running"}}


def test_wrong_method_keeps_the_allow_header() -> None:
    status, headers, body = call(app_with_routes(), "POST", "/conflict")
    assert status == 405
    assert headers["allow"] == "GET"
    assert body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_bad_input_says_which_field() -> None:
    status, _, body = call(app_with_routes(), "GET", "/number", "n=abc")
    assert status == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert [field["field"] for field in body["error"]["details"]["fields"]] == ["query.n"]


def test_bad_input_is_not_echoed_back() -> None:
    _, _, body = call(app_with_routes(), "GET", "/number", "n=abc")
    assert "abc" not in json.dumps(body)


def test_a_crash_answers_500_without_the_detail() -> None:
    status, _, body = call(app_with_routes(), "GET", "/crash")
    assert status == 500
    assert body["error"]["code"] == "INTERNAL"
    assert "secret" not in json.dumps(body)
