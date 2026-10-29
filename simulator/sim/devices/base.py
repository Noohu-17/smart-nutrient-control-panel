"""One MQTT connection per simulated device, with automatic reconnect.

Each device the simulator plays (GW1, ZC1, ...) logs in on its own, with its
device ID as the MQTT client ID and a persistent session (contract 3). When the
broker goes away, the connection retries with a growing delay and logs
"connected" again as soon as the broker is back.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiomqtt

from ..settings import Settings

log = logging.getLogger("sim.devices")

RECONNECT_FIRST_S = 1.0
RECONNECT_MAX_S = 30.0

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
