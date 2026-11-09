"""The stuck-command guard (T1-23, contract §8.1, §8.4, §14).

A command the API sent can be left without replies: the simulator restarted mid-dose, a
controller lost power, a message never arrived. Its last state (DISPATCHED, ACCEPTED or
EXECUTING) would then stand for ever, and the app would show it running. So every SWEEP_S seconds
the worker marks each command that

- the broker took (dispatched_at is set), and that has no final state from the farm (§8.1),
- and about which nothing has been heard for STUCK_AFTER_S: since its expiresAt, or since its
  newest CMD_STATE if that came later (a running dose reports at least every 5 s, §14).

GET /api/commands/{cmdId} then answers UNKNOWN with reason TIMED_OUT, "Result unknown: farm not
responding" (§8.1). UNKNOWN is not final: a newer CMD_STATE from the farm clears the mark, and the
farm's own state shows again. Every time here is the server's clock, so a device's clock can
neither keep a command alive nor end it.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, Engine, and_, exists, func, update
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from ..db.models import CommandReport, SentCommand

log = logging.getLogger("worker")

FINAL_STATES = ("COMPLETED", "PARTIAL", "FAILED", "STOPPED", "REJECTED")  # the farm's final ones
TIMED_OUT = "TIMED_OUT"  # the reason code (§8.4)
STUCK_AFTER_S = 30  # the margin: a running dose is never this long without a report (§14: 5 s)
SWEEP_S = 10

# As in main.py: the database can't answer right now, or isn't migrated to 0005 yet.
_NOT_READY = (OperationalError, InterfaceError, ProgrammingError)


def heard(connection: Connection, org_id: str, site_id: str, cmd_id: str, at: datetime) -> None:
    """A newer CMD_STATE was just stored for the command: it's alive, whatever the guard thought."""
    connection.execute(
        update(SentCommand)
        .where(SentCommand.org_id == org_id, SentCommand.site_id == site_id, SentCommand.cmd_id == cmd_id)
        .values(last_report_at=at, timed_out_at=None)
    )


def mark_stuck(engine: Engine, org_id: str, site_id: str, now: datetime) -> list[tuple[str, str]]:
    """Mark every stuck command timed out, in one statement; (cmdId, kind) of each newly marked."""
    final = exists().where(
        CommandReport.org_id == SentCommand.org_id,
        CommandReport.site_id == SentCommand.site_id,
        CommandReport.cmd_id == SentCommand.cmd_id,
        CommandReport.state.in_(FINAL_STATES),
    )
    # GREATEST skips a null: with no report yet, the silence counts from expiresAt.
    silent_since = func.greatest(SentCommand.expires_at, SentCommand.last_report_at)
    with engine.begin() as connection:
        marked = connection.execute(
            update(SentCommand)
            .where(
                and_(
                    SentCommand.org_id == org_id,
                    SentCommand.site_id == site_id,
                    SentCommand.dispatched_at.is_not(None),
                    SentCommand.timed_out_at.is_(None),
                    silent_since <= now - timedelta(seconds=STUCK_AFTER_S),
                    ~final,
                )
            )
            .values(timed_out_at=now)
            .returning(SentCommand.cmd_id, SentCommand.kind)
        ).all()
    return [(row.cmd_id, row.kind) for row in marked]


def _first_line(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc)).partition("\n")[0]


async def guard(engine: Engine, org_id: str, site_id: str) -> None:
    """Look for stuck commands every SWEEP_S seconds until cancelled. It waits before its first
    look, and a database that can't answer is simply asked again next time."""
    while True:
        await asyncio.sleep(SWEEP_S)
        try:
            marked = await asyncio.to_thread(mark_stuck, engine, org_id, site_id, datetime.now(UTC))
        except _NOT_READY as exc:
            log.warning("stuck-command guard: database not ready (%s); looking again in %d s", _first_line(exc), SWEEP_S)
            continue
        except Exception:  # the guard must never stop the worker
            log.exception("stuck-command guard failed; looking again in %d s", SWEEP_S)
            continue
        for cmd_id, kind in marked:
            log.warning(
                "%s %s: no reply from the farm for %d s after it expired or last reported; "
                "now UNKNOWN, %s", kind, cmd_id, STUCK_AFTER_S, TIMED_OUT,
            )
