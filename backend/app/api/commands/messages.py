"""The cloud → farm commands this API sends (contract §7): each one's envelope, target and topic."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from ..schemas import contract_time

COMMAND_EXPIRY = timedelta(seconds=60)  # "60 s from issuedAt" (§14)
STOP_EXPIRY = timedelta(minutes=5)  # STOP ALL_OPERATIONS and ZONE: a late stop is still wanted (§7.4)

StopScope = Literal["ALL_OPERATIONS", "ZONE"]


@dataclass(frozen=True)
class Command:
    cmd_id: str
    topic: str
    body: dict[str, Any]


def _command(
    site: str,
    kind: str,
    target: dict[str, str],
    topic: str,
    now: datetime,
    expiry: timedelta,
    **fields: Any,
) -> Command:
    """One command in the §7.1 envelope: device CLOUD, and no bootId or seq, as in its example.

    issuedBy, which is for the audit trail only, is left out until people sign in.
    """
    cmd_id = str(uuid.uuid4())
    issued_at = contract_time(now)
    body = {
        "v": 1,
        "type": "CMD",
        "msgId": str(uuid.uuid4()),
        "ts": issued_at,
        "site": site,
        "device": "CLOUD",
        "cmdId": cmd_id,
        "kind": kind,
        "issuedAt": issued_at,
        "expiresAt": contract_time(now + expiry),
        "target": target,
        **fields,
    }
    return Command(cmd_id, topic, body)


def stop(site: str, scope: StopScope, zone: str | None, now: datetime) -> Command:
    """STOP (§7.4), to every controller on farm/{site}/stop; each acts on the parts it owns."""
    if (scope == "ZONE") != (zone is not None):
        raise ValueError("a STOP names a zone with scope ZONE, and only then")
    target = {"scope": scope} if zone is None else {"scope": scope, "zone": zone}
    return _command(site, "STOP", target, f"farm/{site}/stop", now, STOP_EXPIRY)


def resume_zone(site: str, zone: str, now: datetime) -> Command:
    """RESUME (§7.8) for one zone, to its controller."""
    return _command(site, "RESUME", {"zone": zone}, f"farm/{site}/zone/{zone}/cmd", now, COMMAND_EXPIRY)


def resume_climate(site: str, now: datetime) -> Command:
    """RESUME (§7.8) for climate, to CC1."""
    target = {"scope": "CLIMATE"}
    return _command(site, "RESUME", target, f"farm/{site}/climate/cmd", now, COMMAND_EXPIRY)


def dose(
    site: str,
    zone: str,
    *,
    lines: list[dict[str, Any]],
    order: list[str],
    limits: dict[str, Any],
    run: dict[str, Any],
    now: datetime,
) -> Command:
    """DOSE (§7.2), to the zone's controller: the lines, the order they run in, the limits snapshot,
    and `run`, which holds mixWaitS, maxDurationS and tolerancePct."""
    return _command(
        site,
        "DOSE",
        {"zone": zone},
        f"farm/{site}/zone/{zone}/cmd",
        now,
        COMMAND_EXPIRY,
        lines=lines,
        order=order,
        mixWaitS=run["mixWaitS"],
        limits=limits,
        maxDurationS=run["maxDurationS"],
        tolerancePct=run["tolerancePct"],
    )
