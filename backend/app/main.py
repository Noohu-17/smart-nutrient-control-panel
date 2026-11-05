"""Smart Nutrient Advisor back end (FastAPI). Every route lives under /api."""

import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api.commands import add_command_api
from .api.dose_requests import add_dose_api
from .api.routes import add_read_api
from .config import load_settings
from .errors import add_error_handlers
from .health import UP, check_broker, check_database

settings = load_settings()

app = FastAPI(
    title="Smart Nutrient Advisor API",
    docs_url="/api/docs" if settings.api_docs_enabled else None,
    openapi_url="/api/openapi.json" if settings.api_docs_enabled else None,
    redoc_url=None,
)
add_error_handlers(app)
add_read_api(app)
add_dose_api(app)
add_command_api(app)


@app.get("/api/health")
async def health() -> JSONResponse:
    """Uptime check (contract §11.2, public, no farm data).

    200 with status OK when PostgreSQL and the broker are both up, otherwise 503
    with status DEGRADED. Either way the body says which one is up.
    """
    database, broker = await asyncio.gather(check_database(settings), check_broker(settings))
    ok = database == UP and broker == UP
    return JSONResponse(
        status_code=200 if ok else 503,
        content={"status": "OK" if ok else "DEGRADED", "database": database, "broker": broker},
    )
