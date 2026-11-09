"""Run the worker. From backend/: python -m app.worker.main

Subscribes to everything the client's site sends (contract §4), checks each message against
the contract models and stores it: its values in the reading table, the latest ZONE_STATE,
CLIMATE_STATE, ZONE_TELEMETRY and POWER_STATE of each device, the latest CMD_STATE of each
command, when each device was last seen, and any last will.

A bad message is logged and skipped. If the broker goes away the worker reconnects with a
growing delay. If the database is down or not migrated yet it keeps the message and tries
again with the same delay, so messages wait rather than get lost.

Alongside, the stuck-command guard (stuck.py) marks a sent command that the farm has left without
replies, so it shows UNKNOWN, TIMED_OUT rather than running for ever.
"""

import asyncio
import json
import logging
import math
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiomqtt
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError

from ..config import Settings, load_settings
from ..contract import DeviceStatusLastWill, Envelope, parse_farm_message
from ..db.engine import create_db_engine
from .store import mark_offline, reading_rows, store_message
from .stuck import guard

log = logging.getLogger("worker")

CLIENT_ID = "CLOUD-worker"  # contract §3; only one connection may use it at a time
RETRY_FIRST_S = 1.0
RETRY_MAX_S = 30.0
MAX_WAITING_MESSAGES = 10_000  # while the database is down; beyond this the newest are dropped

# The database can't take anything right now: it's down, restarting, or not migrated yet.
# Any other error is about the message itself, and trying again would never end.
DATABASE_NOT_READY = (OperationalError, InterfaceError, ProgrammingError)


class BadMessage(Exception):
    """A payload that breaks the contract; the text says how."""


def parse_payload(payload: bytes) -> Envelope | DeviceStatusLastWill | None:
    """The checked message, or None if its type has no model yet (commands, events ...).

    Raises BadMessage for anything else, whatever the payload holds.
    """
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError) as exc:  # RecursionError: nested too deep to parse
        raise BadMessage(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BadMessage("not a JSON object")
    kind = data.get("type")
    if not isinstance(kind, str):
        raise BadMessage(f"no message type: {kind!r}")
    try:
        return parse_farm_message(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise BadMessage(f"{kind} from {data.get('device')}: {problems}") from exc
    except ValueError:
        return None


def retry_delay(attempt: int) -> float:
    """Seconds to wait before retry number `attempt` (0, 1, 2 ...)."""
    return min(RETRY_MAX_S, RETRY_FIRST_S * 2**attempt)


def _first_line(exc: Exception) -> str:
    """The database's own message: SQLAlchemy's str(exc) adds the whole statement and its values."""
    return str(getattr(exc, "orig", exc)).partition("\n")[0]


class Worker:
    """Checks and stores messages one at a time, so they land in the order they came."""

    def __init__(
        self,
        engine: Engine,
        org_id: str,
        site: str,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.engine, self.org_id, self.site = engine, org_id, site
        self._sleep = sleep

    async def handle(self, topic: str, payload: bytes) -> None:
        try:
            message = parse_payload(payload)
        except BadMessage as exc:
            log.warning("skipping a bad message on %s: %s", topic, exc)
            return
        received_at = datetime.now(UTC)
        if message is None:
            log.debug("skipping a message on %s: its type has no model yet", topic)
        elif isinstance(message, DeviceStatusLastWill):
            # It names no site, so it's this worker's site, the one in the topic it came on.
            log.info("%s says it is offline", message.device)
            await self._write(
                topic,
                f"the last will of {message.device}",
                lambda: mark_offline(
                    self.engine, self.org_id, self.site, message.device, received_at
                ),
            )
        elif message.site != self.site:
            log.warning("skipping a bad message on %s: it names site %s", topic, message.site)
        else:
            rows = reading_rows(message, self.org_id)
            # Python's JSON parser takes NaN and 1e999, which the contract's numbers (§1) aren't.
            bad = [r["metric"] for r in rows if r["value"] is not None and not math.isfinite(r["value"])]
            if bad:
                log.warning("skipping a bad message on %s: %s not a number", topic, ", ".join(bad))
                return
            stored = await self._write(
                topic,
                f"{message.type} from {message.device}",
                lambda: store_message(self.engine, message, self.org_id, rows, received_at),
            )
            if stored is not None and stored.replay:
                log.info(
                    "dropped a replay: %s from %s, msgId %s",
                    message.type, message.device, message.msg_id,
                )

    async def _write[T](self, topic: str, what: str, write: Callable[[], T]) -> T | None:
        """Run one database write; wait and retry while the database isn't ready."""
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(write)
            except DATABASE_NOT_READY as exc:
                delay = retry_delay(attempt)
                attempt += 1
                log.warning(
                    "database not ready (%s); keeping %s, retrying in %.0f s",
                    _first_line(exc), what, delay,
                )
                await self._sleep(delay)
            except Exception as exc:  # noqa: BLE001 - one message must never stop the worker
                log.warning(
                    "skipping a message on %s the database refused: %s", topic, _first_line(exc)
                )
                return None


async def run(
    settings: Settings,
    *,
    client_id: str = CLIENT_ID,
    client_factory: Callable[..., Any] = aiomqtt.Client,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Connect, subscribe and store messages until cancelled; reconnect whenever the link drops."""
    layout = json.loads((settings.client_dir / "layout.json").read_text(encoding="utf-8"))
    site = layout["site"]
    topic = f"farm/{site}/#"  # CLOUD-* may subscribe to this and not to farm/# (contract §4)
    engine = create_db_engine(settings)
    # As in the seed, org_id is the client's folder name until organisations exist (OPEN-11).
    worker = Worker(engine, settings.client_dir.name, site, sleep=sleep)
    guarding = asyncio.create_task(guard(engine, worker.org_id, site))  # stuck commands (T1-23)
    attempt = 0
    try:
        while True:
            try:
                async with client_factory(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                    username=settings.mqtt_username,
                    password=settings.mqtt_password,
                    identifier=client_id,
                    clean_session=False,
                    protocol=aiomqtt.ProtocolVersion.V311,
                    max_queued_incoming_messages=MAX_WAITING_MESSAGES,
                ) as client:
                    await client.subscribe(topic, qos=1)
                    log.info(
                        "%s connected to %s:%d, subscribed to %s",
                        client_id, settings.mqtt_host, settings.mqtt_port, topic,
                    )
                    attempt = 0
                    async for message in client.messages:
                        await worker.handle(str(message.topic), message.payload)
            except aiomqtt.MqttError as err:
                delay = retry_delay(attempt)
                attempt += 1
                log.warning("%s not connected (%s); retrying in %.0f s", client_id, err, delay)
                await sleep(delay)
    finally:
        guarding.cancel()
        await asyncio.gather(guarding, return_exceptions=True)  # its own end, never this one's
        engine.dispose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # aiomqtt (paho-mqtt) and psycopg's async mode need a selector event loop; Windows defaults
    # to proactor, which stops them working when this runs directly on Windows.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(run(load_settings()))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
