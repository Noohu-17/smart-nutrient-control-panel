"""One MQTT connection per simulated device, with automatic reconnect.

Each device the simulator plays (GW1, ZC1, ...) logs in on its own, with its
device ID as the MQTT client ID and a persistent session (contract 3). When the
broker goes away, the connection retries with a growing delay and logs
"connected" again as soon as the broker is back.

SimDevice adds the heartbeat: DEVICE_STATUS every 30 real seconds, retained,
with a last will saying offline (contract 6.4).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiomqtt

from ..messages import Envelope, device_status, offline_status, status_topic
from ..settings import Settings

log = logging.getLogger("sim.devices")

RECONNECT_FIRST_S = 1.0
RECONNECT_MAX_S = 30.0
HEARTBEAT_S = 30.0  # contract 6.4 and 14; real time, whatever the simulator's speed
PUBLISH_TIMEOUT_S = 10.0

Session = Callable[[aiomqtt.Client], Awaitable[None]]


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
    """

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        heartbeat_s: float = HEARTBEAT_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.id, self.kind = device_id, kind
        self.topic = status_topic(site, device_id)
        self.envelope = Envelope(site, device_id, boot_id)
        self.heartbeat_s = heartbeat_s
        self._now, self._monotonic = now, monotonic
        self._booted = monotonic()

    def status(self) -> dict[str, Any]:
        header = self.envelope.next("DEVICE_STATUS", self._now())
        uptime_s = int(self._monotonic() - self._booted)
        return device_status(header, uptime_s=uptime_s, gateway=self.kind == "GATEWAY")

    def will(self) -> aiomqtt.Will:
        return aiomqtt.Will(self.topic, json.dumps(offline_status(self.id)), qos=1, retain=True)

    async def run(self, settings: Settings, **connect_options: Any) -> None:
        await connect_forever(settings, self.id, self.session, will=self.will(), **connect_options)

    async def session(self, client: aiomqtt.Client) -> None:
        """Send heartbeats until the link drops (raises MqttError) or the task is cancelled."""
        watch = asyncio.create_task(stay_connected(client))
        beat = asyncio.create_task(self._heartbeat(client))
        try:
            done, _ = await asyncio.wait({watch, beat}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            with contextlib.suppress(aiomqtt.MqttError):
                await self._publish(client, offline_status(self.id))
            raise
        finally:
            watch.cancel()
            beat.cancel()
            await asyncio.gather(watch, beat, return_exceptions=True)
        for task in done:
            task.result()

    async def _heartbeat(self, client: aiomqtt.Client) -> None:
        while True:
            await self._publish(client, self.status())
            await asyncio.sleep(self.heartbeat_s)

    async def _publish(self, client: aiomqtt.Client, message: dict[str, Any]) -> None:
        await client.publish(
            self.topic, json.dumps(message), qos=1, retain=True, timeout=PUBLISH_TIMEOUT_S
        )
