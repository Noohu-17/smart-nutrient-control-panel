"""GET /api/alerts and POST /api/alerts/{alertId}/ack (contract §11.2, T1-24).

The worker raises and clears alerts (app/alerts.py); the API lists them and records who
acknowledged one. Acknowledging doesn't clear an alert: it stays until its condition is over.
On a playground site only alerts raised since the last reset are listed (T1-25).
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Header, Path, Query
from sqlalchemy import select, update

from ..db.models import Alert
from ..errors import ApiError
from . import queries
from .pack import read_farm
from .routes import EngineDep, SettingsDep, SiteDep, _database, _errors
from .schemas import AlertInfo, AlertList

MAX_ALERTS = 200
Status = Literal["OPEN", "ACKNOWLEDGED", "CLEARED"]
Severity = Literal["CRITICAL", "WARNING", "INFO"]
NOT_ALLOWED = ("viewer",)  # §13: everyone but a Viewer may acknowledge
ALERTS = Alert.__table__  # rows by column name (alert_id, acked_at …)


def _status(row) -> str:
    if row.cleared_at is not None:
        return "CLEARED"
    return "ACKNOWLEDGED" if row.acked_at is not None else "OPEN"


def _info(row) -> AlertInfo:
    return AlertInfo(
        id=row.alert_id, kind=row.kind, severity=row.severity, status=_status(row), zone=row.zone_id,
        message=row.message, system_action=row.system_action, next_step=row.next_step,
        raised_at=row.raised_at, sim_ts=row.sim_ts, cleared_at=row.cleared_at,
        acknowledged_at=row.acked_at, acknowledged_by=row.acked_by,
    )


def list_alerts(
    settings: SettingsDep,
    engine: EngineDep,
    site: SiteDep,
    status: Annotated[Status | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    zone: Annotated[str | None, Query(max_length=64)] = None,
) -> AlertList:
    """Raised alerts, newest first: OPEN, ACKNOWLEDGED (seen, condition still on) or CLEARED."""
    which = [Alert.org_id == site.org_id, Alert.site_id == site.site_id, Alert.raised_at.is_not(None)]
    if severity is not None:
        which.append(Alert.severity == severity)
    if zone is not None:
        which.append(Alert.zone_id == zone)
    if status == "CLEARED":
        which.append(Alert.cleared_at.is_not(None))
    elif status == "ACKNOWLEDGED":
        which += [Alert.cleared_at.is_(None), Alert.acked_at.is_not(None)]
    elif status == "OPEN":
        which += [Alert.cleared_at.is_(None), Alert.acked_at.is_(None)]
    with _database(engine) as connection:
        if read_farm(settings.client_dir).features.get("playground") is True:
            reset_at = queries.playground_reset_at(connection, site)
            if reset_at is not None:
                which.append(Alert.raised_at > reset_at)
        rows = connection.execute(
            select(ALERTS).where(*which).order_by(Alert.raised_at.desc()).limit(MAX_ALERTS)
        ).all()
    return AlertList(items=[_info(row) for row in rows])


def acknowledge(
    alert_id: Annotated[str, Path(alias="alertId", max_length=64)],
    engine: EngineDep,
    site: SiteDep,
    role: Annotated[str | None, Header(alias="X-Demo-Role", max_length=32)] = None,
) -> AlertInfo:
    """Record that a person has seen the alert. 403 for a Viewer (§13); the alert stays until its
    condition is over. Acknowledging again changes nothing. The role is the demo role the app
    sends until sign-in exists (reminder 88)."""
    if role is not None and role.strip().lower() in NOT_ALLOWED:
        raise ApiError(403, "FORBIDDEN", "Viewers can’t acknowledge alerts")
    the_alert = (Alert.org_id == site.org_id, Alert.site_id == site.site_id, Alert.alert_id == alert_id)
    with _database(engine) as connection:
        row = connection.execute(select(ALERTS).where(*the_alert, Alert.raised_at.is_not(None))).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", f"There is no alert {alert_id}")
        if row.acked_at is None:
            connection.execute(
                update(Alert).where(*the_alert).values(acked_at=datetime.now(UTC), acked_by=role)
            )
            connection.commit()
            row = connection.execute(select(ALERTS).where(*the_alert)).first()
    return _info(row)


def add_alert_api(app: FastAPI) -> None:
    app.add_api_route(
        "/api/alerts", list_alerts, response_model=AlertList, responses=_errors(422, 503), tags=["alerts"]
    )
    app.add_api_route(
        "/api/alerts/{alertId}/ack", acknowledge, methods=["POST"], response_model=AlertInfo,
        responses=_errors(403, 404, 422, 503), tags=["alerts"],
    )
