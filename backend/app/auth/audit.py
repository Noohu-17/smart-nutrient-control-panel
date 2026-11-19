"""The audit log: who did what (contract §11.4, §13, T1-35).

`write` adds a row in the caller's own transaction, so the action and its row are saved together.
`write_after` is for actions that must not wait for the database or fail because of it (a Stop,
a call the simulator has already taken): it saves the row on its own and, if the database can't
take it, logs the action instead.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, insert

from ..api.pack import SiteRef
from ..api.routes import DATABASE_NOT_READY
from ..db.models import AuditLog

log = logging.getLogger("api.audit")


def _row(site: SiteRef, user_id: str | None, role: str | None, action: str, target: str | None,
         detail: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "org_id": site.org_id,
        "site_id": site.site_id,
        "audit_id": str(uuid.uuid4()),
        "at": datetime.now(UTC),
        "user_id": user_id,
        "role": role,
        "action": action,
        "target": target,
        "detail": detail,
    }


def write(connection: Connection, site: SiteRef, user_id: str | None, role: str | None, action: str,
          target: str | None = None, detail: dict[str, Any] | None = None) -> None:
    connection.execute(insert(AuditLog).values(_row(site, user_id, role, action, target, detail)))


def write_after(engine: Engine, site: SiteRef, user_id: str | None, role: str | None, action: str,
                target: str | None = None, detail: dict[str, Any] | None = None) -> None:
    try:
        with engine.begin() as connection:
            write(connection, site, user_id, role, action, target, detail)
    except DATABASE_NOT_READY as exc:
        reason = str(getattr(exc, "orig", exc)).partition("\n")[0]
        log.warning("%s by user %s on %s not in the audit log (%s): %s", action, user_id, target, reason, detail)
