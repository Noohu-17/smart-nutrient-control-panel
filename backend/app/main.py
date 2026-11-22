"""Smart Nutrient Advisor back end (FastAPI). Every route lives under /api.

Every route needs a signed-in user except /api/health and /api/auth/login (app.auth.access.gate).
"""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .api.alerts import add_alert_api
from .api.commands import add_command_api
from .api.dose_requests import add_dose_api
from .api.operations import add_operations_api
from .api.routes import _engine_for, add_read_api, get_pack
from .api.sim import add_sim_api
from .auth.access import gate
from .auth.routes import add_auth_api
from .config import DATABASE, load_settings
from .errors import add_error_handlers
from .health import UP, check_broker, check_database
from .packs import PackError
from .push.routes import add_push_api
from .setup.routes import add_setup_api

settings = load_settings()


START_UP_RETRY_S = 10.0


def _load_settings_now(retry_s: float = START_UP_RETRY_S) -> None:
    """Read the farm's settings at start-up (T1-39), so Stop has them even if the database goes
    away before the first request (§8.3 rule 9), and a new version's DEVICE_CONFIG goes out. If the
    database isn't answering yet, keep trying in the background until a version is loaded."""
    while True:
        try:
            get_pack(settings, _engine_for(settings))
            return
        except (PackError, HTTPException) as exc:
            logging.getLogger("api").warning(
                "farm settings not loaded at start-up: %s; trying again in %.0f s", exc, retry_s
            )
        threading.Event().wait(retry_s)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings.config_source == DATABASE:
        asyncio.get_running_loop().run_in_executor(None, _load_settings_now)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Smart Nutrient Advisor API",
    docs_url="/api/docs" if settings.api_docs_enabled else None,
    openapi_url="/api/openapi.json" if settings.api_docs_enabled else None,
    redoc_url=None,
    dependencies=[Depends(gate)],
)
add_error_handlers(app)
add_auth_api(app)
add_push_api(app)
add_read_api(app)
add_dose_api(app)
add_command_api(app)
add_sim_api(app)
add_alert_api(app)
add_setup_api(app)
add_operations_api(app)


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
