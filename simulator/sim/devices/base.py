"""One MQTT connection per simulated device, with automatic reconnect.

Each device the simulator plays (GW1, ZC1, ...) logs in on its own, with its
device ID as the MQTT client ID and a persistent session (contract 3). When the
broker goes away, the connection retries with a growing delay and logs
"connected" again as soon as the broker is back.

SimDevice adds the heartbeat: DEVICE_STATUS every 30 real seconds, retained,
with a last will saying offline (contract 6.4). Everything a device sends goes through
one outbox, so seq follows publish order whatever the message type (contract 5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiomqtt

from ..clock import SimClock
from ..messages import Envelope, device_status, offline_status, status_topic
from ..settings import Settings

log = logging.getLogger("sim.devices")

RECONNECT_FIRST_S = 1.0
RECONNECT_MAX_S = 30.0
HEARTBEAT_S = 30.0  # contract 6.4 and 14; real time, whatever the simulator's speed
PUBLISH_TIMEOUT_S = 10.0
OUTBOX_MAX = 256  # messages held while the broker is away; beyond this the oldest are dropped

Session = Callable[[aiomqtt.Client], Awaitable[None]]


@dataclass
class Outgoing:
    """A message waiting to go out; `build` gives it its envelope when its turn comes."""

    topic: str
    build: Callable[[], dict[str, Any]]
    qos: int
    retain: bool
    payload: str | None = None  # kept, so a retry sends the same msgId and seq


def reconnect_delay(attempt: int) -> float:
    """Seconds to wait before reconnect attempt number `attempt` (0, 1, 2 ...)."""
    return min(RECONNECT_MAX_S, RECONNECT_FIRST_S * 2**attempt)


async def stay_connected(client: aiomqtt.Client) -> None:
    """Default session: wait until the connection drops (aiomqtt then raises MqttError)."""
    async for _ in client.messages:
        pass


async def connect_forever(
    settings: Settings,
    client_id: str,
    session: Session = stay_connected,
    *,
    will: aiomqtt.Will | None = None,
    client_factory: Callable[..., Any] = aiomqtt.Client,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Connect as `client_id` and run `session`; reconnect whenever the link drops.

    Returns only when `session` returns normally. Cancelling the task stops it.
    """
    username, password = settings.login(client_id)
    attempt = 0
    while True:
        try:
            async with client_factory(
                hostname=settings.mqtt_host,
                port=settings.mqtt_port,
                identifier=client_id,
                username=username,
                password=password,
                will=will,
                clean_session=False,
                protocol=aiomqtt.ProtocolVersion.V311,
            ) as client:
                log.info("%s connected to %s:%d", client_id, settings.mqtt_host, settings.mqtt_port)
                attempt = 0
                await session(client)
                return
        except aiomqtt.MqttError as err:
            delay = reconnect_delay(attempt)
            attempt += 1
            log.warning("%s not connected (%s); retrying in %.0f s", client_id, err, delay)
            await sleep(delay)


class SimDevice:
    """One simulated device with its own login, heartbeat and last will.

    The status topic is retained, so a new subscriber sees each device's
    current status at once. If the simulator dies, the broker publishes the
    last will (offline). A clean stop discards the will, so the device then
    publishes the same offline status itself.

    On playground sites pass the simulator's clock: every message then also
    carries simTs. Heartbeats stay on real time whatever the clock's speed.
    """

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        clock: SimClock | None = None,
        heartbeat_s: float = HEARTBEAT_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.id, self.kind = device_id, kind
        self.topic = status_topic(site, device_id)
        self.envelope = Envelope(site, device_id, boot_id)
        self.clock = clock
        self.heartbeat_s = heartbeat_s
        self._now, self._monotonic = now, monotonic
        self._booted = monotonic()
        self._outbox: deque[Outgoing] = deque(maxlen=OUTBOX_MAX)
        self._in_flight: Outgoing | None = None
        self._has_mail = asyncio.Event()
        self._warned_full = False
        self._had_session = False

    def status(self) -> dict[str, Any]:
        sim_ts = self.clock.now() if self.clock else None
        header = self.envelope.next("DEVICE_STATUS", self._now(), sim_ts)
        uptime_s = int(self._monotonic() - self._booted)
        return device_status(header, uptime_s=uptime_s, gateway=self.kind == "GATEWAY")

    def will(self) -> aiomqtt.Will:
        return aiomqtt.Will(self.topic, json.dumps(offline_status(self.id)), qos=1, retain=True)

    async def run(self, settings: Settings, **connect_options: Any) -> None:
        await connect_forever(settings, self.id, self.session, will=self.will(), **connect_options)

    def send(
        self, topic: str, build: Callable[[], dict[str, Any]], *, qos: int, retain: bool
    ) -> None:
        """Queue one message. It goes out in turn, as soon as the device is connected."""
        if len(self._outbox) == OUTBOX_MAX and not self._warned_full:
            log.warning("%s outbox full; dropping the oldest messages", self.id)
            self._warned_full = True
        self._outbox.append(Outgoing(topic, build, qos, retain))
        self._has_mail.set()

    def on_reconnect(self) -> None:
        """The link is back after a drop: a device with retained state sends it again."""

    async def session(self, client: aiomqtt.Client) -> None:
        """Run the device until the link drops (raises MqttError) or the task is cancelled."""
        if self._had_session:
            self.on_reconnect()
        self._had_session = True
        workers = {
            asyncio.create_task(stay_connected(client)),
            asyncio.create_task(self._heartbeat()),
            asyncio.create_task(self._publisher(client)),
        }
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            await _stop(workers)  # nothing may follow the offline status
            with contextlib.suppress(aiomqtt.MqttError):
                await self._publish(client, offline_status(self.id))
            raise
        finally:
            await _stop(workers)
        for task in done:
            task.result()

    async def _heartbeat(self) -> None:
        while True:
            self.send(self.topic, self.status, qos=1, retain=True)
            await asyncio.sleep(self.heartbeat_s)

    async def _publisher(self, client: aiomqtt.Client) -> None:
        """Send the outbox in order. A message that fails stays first for the next session."""
        self._warned_full = False
        while True:
            if self._in_flight is None:
                if not self._outbox:
                    self._has_mail.clear()
                    await self._has_mail.wait()
                    continue
                self._in_flight = self._outbox.popleft()
                self._in_flight.payload = json.dumps(self._in_flight.build())
            item = self._in_flight
            await client.publish(
                item.topic, item.payload, qos=item.qos, retain=item.retain,
                timeout=PUBLISH_TIMEOUT_S,
            )
            self._in_flight = None

    async def _publish(self, client: aiomqtt.Client, message: dict[str, Any]) -> None:
        await client.publish(
            self.topic, json.dumps(message), qos=1, retain=True, timeout=PUBLISH_TIMEOUT_S
        )


async def _stop(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
