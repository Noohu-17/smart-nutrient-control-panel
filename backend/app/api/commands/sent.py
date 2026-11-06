"""The sent_command table: the commands this API has sent, so GET /api/commands/{cmdId} can answer
DISPATCHED until the farm's first CMD_STATE (contract §8.1). A confirmed dose has its own,
stricter path in confirm.py."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, insert, select

from ...db.models import SentCommand
from ..pack import SiteRef
from ..routes import DATABASE_NOT_READY
from .messages import Command

log = logging.getLogger("api")


def row(site: SiteRef, command: Command) -> dict[str, Any]:
    """The command's sent_command row, not yet dispatched."""
    return {
        "org_id": site.org_id,
        "site_id": site.site_id,
        "cmd_id": command.cmd_id,
        "kind": command.body["kind"],
        "topic": command.topic,
        "body": command.body,
        "issued_at": datetime.fromisoformat(command.body["issuedAt"]),
        "expires_at": datetime.fromisoformat(command.body["expiresAt"]),
    }


def record(engine: Engine, site: SiteRef, command: Command, dispatched_at: datetime) -> None:
    """Keep a stop or resume once the broker has taken it.

    It is saved after it is sent, so sending a stop never waits for the database (§8.3 rule 9). A
    database that can't take it changes nothing for the farm: GET /api/commands/{cmdId} then
    follows the command from its first CMD_STATE.
    """
    try:
        with engine.begin() as connection:
            connection.execute(insert(SentCommand).values({**row(site, command), "dispatched_at": dispatched_at}))
    except DATABASE_NOT_READY as exc:
        reason = str(getattr(exc, "orig", exc)).partition("\n")[0]
        log.warning("%s %s sent, but not recorded: %s", command.body["kind"], command.cmd_id, reason)


def dispatched(connection: Connection, site: SiteRef, cmd_id: str) -> bool:
    """True once the broker has taken the command."""
    found = connection.scalar(
        select(SentCommand.cmd_id).where(
            SentCommand.org_id == site.org_id,
            SentCommand.site_id == site.site_id,
            SentCommand.cmd_id == cmd_id,
            SentCommand.dispatched_at.is_not(None),
        )
    )
    return found is not None
