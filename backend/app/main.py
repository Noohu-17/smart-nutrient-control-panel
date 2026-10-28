"""Smart Nutrient Advisor back end (FastAPI). Every route lives under /api."""

import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import load_settings
from .health import UP, check_broker, check_database

settings = load_settings()

app = FastAPI(
    title="Smart Nutrient Advisor API",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


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
