"""Error answers in the contract's shape (§11.1): {"error": {"code", "message", "details"}}.

Without these handlers FastAPI answers {"detail": ...}, which the app cannot read.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


def _error(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return _error(exc.status_code, HTTPStatus(exc.status_code).name, str(exc.detail), headers=exc.headers)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Field and reason only: echoing the input could send a password back in the answer.
    fields = [{"field": ".".join(str(part) for part in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return _error(422, "VALIDATION_ERROR", "The request is not valid", {"fields": fields})


async def _crash(_: Request, __: Exception) -> JSONResponse:
    # The server logs the traceback. The details stay out of the answer.
    return _error(500, "INTERNAL", "Something went wrong on the server")


def add_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _crash)
