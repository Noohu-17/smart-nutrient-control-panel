"""Confirming a reviewed dose: POST /api/dose-requests/{id}/confirm (contract §11.1, §11.3).

The dose is checked again first, with the zone as it is now, because the review may be minutes
old. The DOSE command is then saved in sent_command *before* it is sent, so a dose request keeps
that one command and its cmdId whatever fails afterwards:

- the same Idempotency-Key again gets the first answer, and nothing is sent again (§11.1);
- after a 503 (the broker didn't take it), the same key sends the same command again, until it
  expires. A controller never runs a cmdId twice (§8.3 rule 1), so even a command the broker
  took without saying so is dosed only once;
- any other confirm of the same dose request is 409: a new dose needs a new review.

Confirms of one dose request wait for each other, sending included, so two at once send it once.
"""

import json
import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, Engine, Row, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ...config import Settings
from ...db.models import DoseRequest, SentCommand
from ...errors import ApiError
from .. import queries
from ..dosing import DoseInputError, review, validate_lines
from ..pack import PackError, SiteRef, read_dosing, read_farm
from ..routes import DATABASE_NOT_READY, _database, _not_loaded, _zone
from ..schemas import DoseCheck, DoseLineAmount
from . import messages, sent
from .publish import NotSent, Publisher
from .schemas import CommandSent

log = logging.getLogger("api")

DAY = timedelta(hours=24)
RUN_SETTINGS = ("mixWaitS", "maxDurationS", "tolerancePct")  # limits.json zones.{zone}.dose (§7.2)


def confirm(
    settings: Settings,
    engine: Engine,
    site: SiteRef,
    publisher: Publisher,
    request_id: str,
    key: str | None,
) -> CommandSent:
    """Check the dose again and send it: the answer once the broker has it, or the first answer again."""
    with _database(engine) as connection, _one_at_a_time(connection, site, request_id):
        command = _command_of(connection, site, request_id)
        if command is None:
            _save(connection, site, request_id, key, _checked_dose(connection, settings, site, request_id))
            command = _command_of(connection, site, request_id)
        elif key is None or key != command.idempotency_key:
            raise HTTPException(
                409, f"Dose request {request_id} is already confirmed, as command {command.cmd_id}. Nothing was sent"
            )
        elif command.dispatched_at is not None:
            return CommandSent(cmd_id=command.cmd_id, state="DISPATCHED")  # a repeat: the first answer
        elif datetime.now(UTC) >= command.expires_at:
            raise HTTPException(
                409, f"Command {command.cmd_id} expired before the broker took it, so it was not sent. Review the dose again"
            )
        _dispatch(connection, publisher, site, command)
    return CommandSent(cmd_id=command.cmd_id, state="DISPATCHED")


@contextmanager
def _one_at_a_time(connection: Connection, site: SiteRef, request_id: str) -> Iterator[None]:
    """Hold a lock on the dose request for the whole confirm, sending included.

    A session lock (pg_advisory_lock), because the command is committed before it is sent, and a
    commit would end a row lock.
    """
    lock_id = func.hashtextextended(f"dose_request/{site.org_id}/{site.site_id}/{request_id}", 0)
    connection.execute(select(func.pg_advisory_lock(lock_id)))
    try:
        yield
    finally:
        try:
            connection.rollback()
            connection.execute(select(func.pg_advisory_unlock(lock_id)))
            connection.commit()
        except DATABASE_NOT_READY:
            connection.invalidate()  # closing the connection ends the session, and its lock with it


def _command_of(connection: Connection, site: SiteRef, request_id: str) -> Row[Any] | None:
    return connection.execute(
        select(
            SentCommand.cmd_id,
            SentCommand.topic,
            SentCommand.body,
            SentCommand.expires_at,
            SentCommand.dispatched_at,
            SentCommand.idempotency_key,
        ).where(
            SentCommand.org_id == site.org_id,
            SentCommand.site_id == site.site_id,
            SentCommand.dose_request_id == request_id,
        )
    ).first()


def _refuse_if_blocked(checks: Iterable[DoseCheck]) -> None:
    """409 with the first BLOCK check's ID and message, as the app's fake answers (T2-11)."""
    blocked = next((check for check in checks if check.result == "BLOCK"), None)
    if blocked is not None:
        raise ApiError(409, blocked.id, blocked.message)


def _checked_dose(
    connection: Connection, settings: Settings, site: SiteRef, request_id: str
) -> messages.Command:
    """The DOSE for a dose request, once both its review and a new one of the zone as it is now pass."""
    saved = connection.execute(
        select(DoseRequest).where(
            DoseRequest.org_id == site.org_id,
            DoseRequest.site_id == site.site_id,
            DoseRequest.request_id == request_id,
        )
    ).first()
    if saved is None:
        raise ApiError(404, "NOT_FOUND", f"There is no dose request {request_id}")
    _refuse_if_blocked(DoseCheck.model_validate(check) for check in saved.checks)  # what the person saw

    zone = next((z for z in read_farm(settings.client_dir).zones if z.id == saved.zone_id), None)
    dosing = None if zone is None else read_dosing(settings.client_dir, zone.id)
    if zone is None or dosing is None:
        raise ApiError(404, "NOT_FOUND", f"There is no zone {saved.zone_id}")
    lines = [DoseLineAmount(line=line["line"], ml=line["ml"]) for line in saved.lines]
    try:
        validate_lines(zone, lines)
    except DoseInputError as exc:
        raise ApiError(422, "INVALID_COMMAND", str(exc)) from exc

    # The zone as it is now, gathered as POST /api/dose-requests gathers it.
    now = datetime.now(UTC)
    rows = queries.zones(connection, site, zone.id)
    if not rows:
        raise _not_loaded()
    status = _zone(connection, site, rows[0], zone.source)
    farm_now = status.sim_ts if dosing.simulated_time and status.sim_ts is not None else now
    dosed = queries.delivered_ml_since(
        connection, site, zone.controller, farm_now - DAY, simulated=dosing.simulated_time
    )
    _refuse_if_blocked(review(zone, status, dosing, lines, dosed, now).checks)

    requested = {line.line for line in lines}
    return messages.dose(
        site.site_id,
        zone.id,
        lines=saved.lines,
        order=[line for line in zone.dose_lines if line in requested],  # the layout's order (§2.4)
        limits=saved.limits,  # the snapshot the review used (§7.2)
        run=_run_settings(settings.client_dir, zone.id),
        now=now,
    )


def _run_settings(client_dir: Path, zone_id: str) -> dict[str, Any]:
    """mixWaitS, maxDurationS and tolerancePct for the zone, from limits.json (§2.5, §7.2)."""
    try:
        dose = json.loads((client_dir / "limits.json").read_text(encoding="utf-8"))["zones"][zone_id]["dose"]
        return {key: dose[key] for key in RUN_SETTINGS}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        log.error("cannot load the config pack: %s", exc)
        raise PackError("The farm layout could not be loaded") from exc


def _save(
    connection: Connection, site: SiteRef, request_id: str, key: str | None, command: messages.Command
) -> None:
    """Keep the command before it is sent, so a repeat finds it whatever happens next."""
    try:
        connection.execute(
            insert(SentCommand).values(
                {**sent.row(site, command), "dose_request_id": request_id, "idempotency_key": key}
            )
        )
        connection.commit()
    except IntegrityError as exc:  # the dose request is locked, so it is the key
        connection.rollback()
        raise HTTPException(409, f"Idempotency-Key {key} is already used for another dose request") from exc


def _dispatch(connection: Connection, publisher: Publisher, site: SiteRef, command: Row[Any]) -> None:
    try:
        publisher.publish(command.topic, command.body)
    except NotSent as exc:
        log.warning("DOSE %s not sent on %s: %s", command.cmd_id, command.topic, exc)
        raise HTTPException(503, "The command could not be sent: the broker is not available") from exc
    connection.execute(
        update(SentCommand)
        .where(
            SentCommand.org_id == site.org_id,
            SentCommand.site_id == site.site_id,
            SentCommand.cmd_id == command.cmd_id,
        )
        .values(dispatched_at=datetime.now(UTC))
    )
    connection.commit()
    log.info("sent DOSE %s on %s", command.cmd_id, command.topic)
