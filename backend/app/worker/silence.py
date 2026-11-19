"""The silence sweep (T1-34, contract §11.2, §14): alerts for what the farm stops sending.

Every SWEEP_S seconds the worker looks at each device of its site, as the device table has it
(last_seen, last will) with a zone controller's newest ZONE_TELEMETRY, and raises or clears:

- DEVICE_OFFLINE, for a device heard from at least once whose last will came, or that has sent
  nothing for alerts.json events.DEVICE_OFFLINE.thresholdS;
- READING_STALE, for a zone whose controller is online but whose newest telemetry is older than
  events.READING_STALE.thresholdS.

The rules are in app/alerts.py (silence_conditions), with each kind's delayS. Time is the
server's clock against the messages' ts, as GET /api/zones judges `online` (§14). Power at the
farm comes and goes, so these will fire: the delays keep a short cut from raising them.

Just after the worker starts, every device's newest message may be old only because the worker
was away, not the device: heartbeats come within 30 s. So for the first DEVICE_OFFLINE
thresholdS the sweep only clears, and raises nothing new.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, and_, select
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from .. import alerts
from ..db.models import Device, LatestMessage

log = logging.getLogger("worker")

SWEEP_S = 10

# As in main.py: the database can't answer right now, or isn't migrated yet.
_NOT_READY = (OperationalError, InterfaceError, ProgrammingError)


def devices_seen(connection, org_id: str, site_id: str) -> list[alerts.DeviceSeen]:
    """Every device of the site, with its zone controller's newest ZONE_TELEMETRY time."""
    telemetry = and_(
        LatestMessage.org_id == Device.org_id,
        LatestMessage.site_id == Device.site_id,
        LatestMessage.device_id == Device.device_id,
        LatestMessage.type == "ZONE_TELEMETRY",
    )
    rows = connection.execute(
        select(
            Device.device_id, Device.kind, Device.zone_id, Device.last_seen, Device.last_will_at,
            LatestMessage.ts,
        )
        .outerjoin(LatestMessage, telemetry)
        .where(Device.org_id == org_id, Device.site_id == site_id)
        .order_by(Device.device_id)
    ).all()
    return [alerts.DeviceSeen(*row) for row in rows]


def sweep(
    engine: Engine, org_id: str, site_id: str, rules: alerts.AlertRules, now: datetime,
    *, raising: bool = True,
) -> None:
    """Judge every silence condition of the site once, in one transaction. With raising False
    (just after the worker starts), conditions that are over are cleared and nothing is raised."""
    with engine.begin() as connection:
        for device in devices_seen(connection, org_id, site_id):
            for condition in alerts.silence_conditions(device, rules, now):
                if condition.on and not raising:
                    continue
                alerts.judge(
                    connection, org_id, site_id, device.zone_id, condition,
                    rules.delay(condition.kind), now, None,
                )


def _first_line(exc: Exception) -> str:
    return str(getattr(exc, "orig", exc)).partition("\n")[0]


async def watch(
    engine: Engine,
    org_id: str,
    site_id: str,
    rules: alerts.AlertRules,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Sweep every SWEEP_S seconds until cancelled. A database that can't answer is simply asked
    again next time, and nothing here may stop the worker. For the first DEVICE_OFFLINE
    thresholdS it only clears (see above)."""
    started = clock()
    quiet_for = timedelta(seconds=rules.device_offline_s)
    while True:
        await sleep(SWEEP_S)
        now = clock()
        raising = now - started >= quiet_for
        try:
            await asyncio.to_thread(sweep, engine, org_id, site_id, rules, now, raising=raising)
        except _NOT_READY as exc:
            log.warning("silence sweep: database not ready (%s); looking again in %d s", _first_line(exc), SWEEP_S)
        except Exception:
            log.exception("silence sweep failed; looking again in %d s", SWEEP_S)
