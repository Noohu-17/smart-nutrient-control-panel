"""How GET /api/commands/{cmdId} shows a command the stuck-command guard has marked (T1-23).

The worker's guard (app/worker/stuck.py) marks a sent command that the farm has left without
replies. Its answer is then UNKNOWN, with reason TIMED_OUT (§8.1, §8.4), and keeps whatever the
farm last reported: the device, and the lines delivered so far. A final state from the farm always
wins: it can't be marked, and a newer report clears the mark.
"""

from sqlalchemy import Connection, select

from ...db.models import SentCommand
from ...worker.stuck import FINAL_STATES, TIMED_OUT
from ..pack import SiteRef
from .schemas import CommandStatus


def timed_out(connection: Connection, site: SiteRef, cmd_id: str) -> bool:
    marked_at = connection.scalar(
        select(SentCommand.timed_out_at).where(
            SentCommand.org_id == site.org_id,
            SentCommand.site_id == site.site_id,
            SentCommand.cmd_id == cmd_id,
        )
    )
    return marked_at is not None


def shown(status: CommandStatus, marked: bool) -> CommandStatus:
    if not marked or status.state in FINAL_STATES:
        return status
    # Validated again, so the answer can only ever hold a state its model allows.
    return CommandStatus.model_validate(status.model_dump() | {"state": "UNKNOWN", "reason": TIMED_OUT})
