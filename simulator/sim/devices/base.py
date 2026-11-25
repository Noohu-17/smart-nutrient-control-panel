"""One MQTT connection per simulated device, with automatic reconnect.

Each device the simulator plays (GW1, ZC1, ...) logs in on its own, with its
device ID as the MQTT client ID and a persistent session (contract 3). When the
broker goes away, the connection retries with a growing delay and logs
"connected" again as soon as the broker is back.

SimDevice adds the heartbeat: DEVICE_STATUS every 30 real seconds, retained,
with a last will saying offline (contract 6.4). Everything a device sends goes through
one outbox, so seq follows publish order whatever the message type (contract 5).
A playground drill can stop a device as a crash would (go_offline: silent, last will)
and start it again as a new boot (come_back: next bootId, seq from 1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import socket
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
RECONNECT_JITTER = 0.5  # up to this share comes off each wait, so devices don't retry together
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
    """Longest wait before reconnect attempt number `attempt` (0, 1, 2 ...), in seconds."""
    return min(RECONNECT_MAX_S, RECONNECT_FIRST_S * 2**attempt)


def jittered(delay_s: float, rng: random.Random) -> float:
    """The wait with a random share taken off, so a restarted broker isn't hit all at once."""
    return delay_s * (1.0 - RECONNECT_JITTER * rng.random())


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
    rng: random.Random | None = None,
    ready: Callable[[], Awaitable[Any]] | None = None,
) -> None:
    """Connect as `client_id` and run `session`; reconnect whenever the link drops.

    Returns only when `session` returns normally. Cancelling the task stops it. The jitter is
    seeded with the device ID: each device waits differently, and each run repeats exactly.
    `ready` is awaited before each attempt: a device that is switched off doesn't connect.
    """
    username, password = settings.login(client_id)
    rng = rng or random.Random(client_id)
    attempt = 0
    while True:
        if ready is not None:
            await ready()
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
            delay = jittered(reconnect_delay(attempt), rng)
            attempt += 1
            log.warning("%s not connected (%s); retrying in %.1f s", client_id, err, delay)
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
        self.id, self.kind, self.site = device_id, kind, site
        self.topic = status_topic(site, device_id)
        self.envelope = Envelope(site, device_id, boot_id)
        self.powered = True  # False while a playground drill has stopped it (contract 11.4.3)
        self._power_on = asyncio.Event()
        self._power_on.set()
        self._client: aiomqtt.Client | None = None
        self._status_queued = False
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
        await connect_forever(
            settings, self.id, self.session, will=self.will(), ready=self._power_on.wait,
            **connect_options,
        )

    def go_offline(self) -> None:
        """The device stops (a crash, a hang, its power): it goes silent at once. The link drops
        without a goodbye, so the broker publishes its last will (contract 6.4, 10 rule 6), and
        what it had not sent yet is lost with its RAM."""
        if not self.powered:
            return
        self.powered = False
        self._power_on.clear()
        self._outbox.clear()
        self._in_flight = None
        if self._client is not None:
            drop_link(self._client)
        log.info("%s is offline (playground drill)", self.id)
        self.on_power_lost()

    def come_back(self, boot_id: int) -> None:
        """The device starts again: a new boot, so a new bootId and seq from 1 (contract 5). It
        says it is online first, then does what a starting device does, then reconnects."""
        if self.powered:
            return
        self.powered = True
        self.envelope = Envelope(self.site, self.id, boot_id)
        self._booted = self._monotonic()
        self.send(self.topic, self.status, qos=1, retain=True)
        self._status_queued = True
        log.info("%s is back (bootId %d)", self.id, boot_id)
        self.on_boot()
        self._power_on.set()

    def on_power_lost(self) -> None:
        """What stops with the device, beyond its messages: a controller's outputs."""

    def on_boot(self) -> None:
        """What the device does as it starts again after go_offline()."""

    def send(
        self, topic: str, build: Callable[[], dict[str, Any]], *, qos: int, retain: bool
    ) -> None:
        """Queue one message. It goes out in turn, as soon as the device is connected."""
        if not self.powered:
            return  # a stopped device sends nothing
        if len(self._outbox) == OUTBOX_MAX and not self._warned_full:
            log.warning("%s outbox full; dropping the oldest messages", self.id)
            self._warned_full = True
        self._outbox.append(Outgoing(topic, build, qos, retain))
        self._has_mail.set()

    def on_reconnect(self) -> None:
        """The link is back after a drop: a device with retained state sends it again."""

    def subscriptions(self) -> tuple[str, ...]:
        """The topics this device listens to (contract 4); most devices listen to none."""
        return ()

    def on_message(self, topic: str, payload: bytes | str) -> None:
        """One message on a subscribed topic."""

    async def session(self, client: aiomqtt.Client) -> None:
        """Run the device until the link drops (raises MqttError) or the task is cancelled."""
        if self._had_session:
            self.on_reconnect()
        self._had_session = True
        self._client = client
        workers = {
            asyncio.create_task(self._listen(client)),
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
            self._client = None
            await _stop(workers)
        for task in done:
            task.result()

    async def _listen(self, client: aiomqtt.Client) -> None:
        """Subscribe (again after every reconnect) and hand each message on. Ends with the link."""
        for topic in self.subscriptions():
            await client.subscribe(topic, qos=1)
        async for message in client.messages:
            self.on_message(str(message.topic), message.payload)

    async def _heartbeat(self) -> None:
        while True:
            if self._status_queued:
                self._status_queued = False  # come_back() already queued this one
            else:
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


def drop_link(client: aiomqtt.Client) -> None:
    """Close the connection without MQTT's DISCONNECT, as a crash would, so the broker publishes
    the last will. aiomqtt has no public call for this, so it shuts down paho's socket for
    sending: the broker sees the connection end and closes it, aiomqtt sees a lost connection,
    and the session ends with MqttError as on any drop."""
    sock = client._client.socket()  # noqa: SLF001
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.shutdown(socket.SHUT_WR)


async def _stop(tasks: set[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
