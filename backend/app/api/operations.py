"""GET /api/operations (contract §11.2, T1-28): every action a person took on the farm, newest first.

Four kinds, each with who did it, when, and what came of it:

- DOSE, STOP and RESUME: the commands this API sent (sent_command), once the broker had them. The
  result is the command's state as GET /api/commands/{cmdId} shows it (its newest CMD_STATE, or
  DISPATCHED until then, or UNKNOWN, TIMED_OUT), and a dose has its lines and EC and pH before and
  after (§6.6).
- ACK: an alert someone acknowledged (POST /api/alerts/{alertId}/ack).

Who is the user the command names in issuedBy, or who acknowledged the alert, with their name and
the role the audit log recorded when they did it. A stop's session is checked without the database
(app.auth.access.stop_caller), so its audit row has no role: a stop shows the role the user has now.
A stop sent while the database was down was never recorded (sent.py), so it isn't listed; its log
line says who sent it.

Every signed-in role may read it (§11.2). One page is `limit` actions, newest first by time and
then by ID; `next` is where the next, older page starts, passed back as `before`, so every action
shows exactly once even when two have the same time. `zone` keeps the actions on that zone,
site-wide stops included, as they stop it too.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query
from sqlalchemy import Connection, and_, or_, select

from ..db.models import Alert, AuditLog, CommandReport, SentCommand, User
from ..errors import ApiError
from .commands import stuck
from .commands.schemas import CommandStatus
from .pack import SiteRef
from .routes import EngineDep, SiteDep, _database, _errors
from .schemas import Operation, OperationAlert, OperationBy, OperationList, OperationResult

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
Kind = Literal["DOSE", "STOP", "RESUME", "ACK"]
COMMANDS = ("DOSE", "STOP", "RESUME")
# The audit log's action for each command kind (confirm.py, sent.py) and for an acknowledgement.
AUDIT_ACTION = {"DOSE": "DOSE_CONFIRM", "STOP": "STOP", "RESUME": "RESUME", "ACK": "ALERT_ACK"}
# A page's end: the time of its last action and, after "~", that action's ID (a cmdId or an alertId).
Cursor = tuple[datetime, str | None]


def _roles_then(connection: Connection, site: SiteRef, page: list[Operation]) -> dict[tuple[str, str], str]:
    """The role the audit log recorded for each action on the page: the role when it was done. One
    query for the whole page, as the audit log has no index on its target."""
    keys = {(AUDIT_ACTION[operation.kind], _id(operation)) for operation in page}
    if not keys:
        return {}
    rows = connection.execute(
        select(AuditLog.action, AuditLog.target, AuditLog.role)
        .where(
            AuditLog.org_id == site.org_id,
            AuditLog.site_id == site.site_id,
            AuditLog.action.in_({action for action, _ in keys}),
            AuditLog.target.in_({target for _, target in keys}),
            AuditLog.role.is_not(None),
        )
        .order_by(AuditLog.at)
    ).all()
    roles: dict[tuple[str, str], str] = {}
    for row in rows:
        roles.setdefault((row.action, row.target), row.role)  # the first: when it was done
    return roles


def _with_role_then(operation: Operation, roles: dict[tuple[str, str], str]) -> Operation:
    role = roles.get((AUDIT_ACTION[operation.kind], _id(operation)))
    if operation.by is None or role is None:
        return operation  # keeps the role the user has now
    return operation.model_copy(update={"by": operation.by.model_copy(update={"role": role})})


def _same_user(table, user_id):
    """Join the user table on `user_id`, a column of `table`, in the same organisation and site."""
    return and_(User.org_id == table.org_id, User.site_id == table.site_id, User.user_id == user_id)


def _bytes(key):
    """The ID compared byte by byte, as Python compares the two kinds when it merges them, whatever
    the database's own collation would do with the hyphens."""
    return key.collate("C")


def _older(at, key, cursor: Cursor):
    """Rows after the cursor, newest first by time and then by ID."""
    moment, after_id = cursor
    if after_id is None:
        return at < moment
    return or_(at < moment, and_(at == moment, _bytes(key) < after_id))


def _commands(
    connection: Connection, site: SiteRef, kind: Kind | None, zone: str | None, cursor: Cursor | None, limit: int
) -> list[Operation]:
    which = [
        SentCommand.org_id == site.org_id,
        SentCommand.site_id == site.site_id,
        SentCommand.dispatched_at.is_not(None),  # a dose the broker never took didn't happen
    ]
    if kind is not None:
        which.append(SentCommand.kind == kind)
    if zone is not None:
        target = SentCommand.body["target"]
        which.append(or_(
            target["zone"].astext == zone,
            and_(SentCommand.kind == "STOP", target["scope"].astext != "ZONE"),  # it stops every zone
        ))
    if cursor is not None:
        which.append(_older(SentCommand.issued_at, SentCommand.cmd_id, cursor))
    rows = connection.execute(
        select(
            SentCommand.cmd_id, SentCommand.kind, SentCommand.body, SentCommand.issued_at, SentCommand.issued_by,
            SentCommand.timed_out_at, CommandReport.body.label("report"), User.name.label("user_name"),
            User.role.label("user_role"),
        )
        .outerjoin(CommandReport, and_(
            CommandReport.org_id == SentCommand.org_id, CommandReport.site_id == SentCommand.site_id,
            CommandReport.cmd_id == SentCommand.cmd_id,
        ))
        .outerjoin(User, _same_user(SentCommand, SentCommand.issued_by))
        .where(*which)
        .order_by(SentCommand.issued_at.desc(), _bytes(SentCommand.cmd_id).desc())
        .limit(limit)
    ).all()
    return [_command(row) for row in rows]


def _command(row: Any) -> Operation:
    if row.report is None:  # the broker has it, and nothing heard from the farm yet (§8.1)
        status = CommandStatus.dispatched(row.cmd_id)
    else:
        status = CommandStatus.model_validate(row.report)
    status = stuck.shown(status, row.timed_out_at is not None)
    target = row.body.get("target") or {}
    dose = row.kind == "DOSE"
    return Operation(
        kind=row.kind,
        at=row.issued_at,
        by=_by(row.issued_by, row.user_name, row.user_role),
        zone=target.get("zone"),
        target=target,
        cmd_id=row.cmd_id,
        result=OperationResult(state=status.state, reason=status.reason),
        lines=status.lines if dose else None,
        before=status.before if dose else None,
        after=status.after if dose else None,
        alert=None,
    )


def _acks(
    connection: Connection, site: SiteRef, zone: str | None, cursor: Cursor | None, limit: int
) -> list[Operation]:
    which = [Alert.org_id == site.org_id, Alert.site_id == site.site_id, Alert.acked_at.is_not(None)]
    if zone is not None:
        which.append(Alert.zone_id == zone)
    if cursor is not None:
        which.append(_older(Alert.acked_at, Alert.alert_id, cursor))
    rows = connection.execute(
        select(
            Alert.alert_id, Alert.kind, Alert.severity, Alert.message, Alert.zone_id, Alert.acked_at,
            Alert.acked_by, User.name.label("user_name"), User.role.label("user_role"),
        )
        .outerjoin(User, _same_user(Alert, Alert.acked_by))
        .where(*which)
        .order_by(Alert.acked_at.desc(), _bytes(Alert.alert_id).desc())
        .limit(limit)
    ).all()
    return [
        Operation(
            kind="ACK",
            at=row.acked_at,
            by=_by(row.acked_by, row.user_name, row.user_role),
            zone=row.zone_id,
            target=None,
            cmd_id=None,
            result=OperationResult(state="ACKNOWLEDGED", reason=None),
            lines=None,
            before=None,
            after=None,
            alert=OperationAlert(id=row.alert_id, kind=row.kind, severity=row.severity, message=row.message),
        )
        for row in rows
    ]


def _by(user_id: str | None, name: str | None, role: str | None) -> OperationBy | None:
    return None if user_id is None else OperationBy(id=user_id, name=name, role=role)


def _id(operation: Operation) -> str:
    return operation.cmd_id or (operation.alert.id if operation.alert else "")


def _next(operation: Operation) -> str:
    """`next`: the last action's time, to the microsecond, in UTC, and its ID."""
    moment = operation.at.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return f"{moment}~{_id(operation)}"


def _cursor(before: str) -> Cursor:
    """`before`: a time (actions before it), or a page's `next` (actions after that one)."""
    moment_text, _, after_id = before.partition("~")
    try:
        moment = datetime.fromisoformat(moment_text)
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "before must be a time, or the next of a page") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)  # a time without a zone is UTC, as every time in the API
    return moment, after_id or None


def list_operations(
    engine: EngineDep,
    site: SiteDep,
    kind: Annotated[Kind | None, Query()] = None,
    zone: Annotated[str | None, Query(max_length=64)] = None,
    before: Annotated[str | None, Query(max_length=120, description="A time, or a page's `next`")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> OperationList:
    """Every dose, stop, resume and alert acknowledgement, newest first, with who did it, when and
    what came of it. `next` is null when there is nothing older."""
    cursor = _cursor(before) if before is not None else None
    with _database(engine) as connection:
        # One more than a page from each, so we know whether an older page exists.
        found = []
        if kind is None or kind in COMMANDS:
            found += _commands(connection, site, kind, zone, cursor, limit + 1)
        if kind is None or kind == "ACK":
            found += _acks(connection, site, zone, cursor, limit + 1)
        found.sort(key=lambda operation: (operation.at, _id(operation)), reverse=True)
        page = found[:limit]
        roles = _roles_then(connection, site, page)
    page = [_with_role_then(operation, roles) for operation in page]
    return OperationList(items=page, next=_next(page[-1]) if len(found) > limit else None)


def add_operations_api(app: FastAPI) -> None:
    app.add_api_route(
        "/api/operations", list_operations, response_model=OperationList, responses=_errors(401, 422, 503),
        tags=["operations"],
    )
