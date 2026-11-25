"""The service's own link to the rig's gateway: its sf/* topics on the broker (gateway CLOUD.md).

One MQTT login (RIG_LINK_ID) subscribes to `<telemetry topic>/sf/#` and publishes commands on
`<command topic>/sf/cmd`. Each document goes to the zone it is about. QoS 1 repeats are dropped
by (gateway, boot, schema, seq). A retained message is old news, not a sign of life: only live
messages keep a zone online, and a live message from a new gateway `boot` means the rig started
again.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Iterable
from typing import Any

import aiomqtt

from ..devices.base import PUBLISH_TIMEOUT_S, connect_forever
from ..settings import Settings
from . import sf
from .config import RigSettings
from .device import RigZoneDevice

log = logging.getLogger("sim.rig.link")

COMMANDS_HELD = 64  # held while the broker is away; the rig refuses any older than 60 s anyway


class RigLink:
    def __init__(self, rig: RigSettings, devices: Iterable[RigZoneDevice] = ()):
        self.rig = rig
        self.devices: list[RigZoneDevice] = list(devices)
        self._repeats = sf.Repeats()
        self._boot: dict[str, int] = {}  # each gateway's boot, from its live messages
        self._outbox: deque[dict[str, Any]] = deque(maxlen=COMMANDS_HELD)
        self._has_mail = asyncio.Event()

    def send(self, command: dict[str, Any]) -> None:
        """One sf/cmd/1 to the gateway, in order, as soon as the link is up."""
        log.info("-> rig %s %s %s", command.get("cmd"), command.get("target"),
                 json.dumps(command.get("args"), sort_keys=True))
        if len(self._outbox) == COMMANDS_HELD:
            dropped = self._outbox[0]
            log.warning("%d rig commands held while the broker is away; dropping the oldest: "
                        "%s %s %s", COMMANDS_HELD, dropped.get("cmd"), dropped.get("target"),
                        dropped.get("id"))
        self._outbox.append(command)
        self._has_mail.set()

    async def run(self, settings: Settings) -> None:
        await connect_forever(settings, self.rig.link_id, self._session)

    async def _session(self, client: aiomqtt.Client) -> None:
        """Until the link drops: listening and publishing both end the session if they fail, so
        a publish that times out reconnects (as SimDevice.session) with its command still first."""
        await client.subscribe(f"{self.rig.telemetry_topic}/sf/#", qos=1)
        workers = {asyncio.create_task(self._listen(client)),
                   asyncio.create_task(self._publisher(client))}
        try:
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        for task in done:
            task.result()  # MqttError: connect_forever connects again

    async def _listen(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            self.on_message(str(message.topic), message.payload, retained=bool(message.retain))

    async def _publisher(self, client: aiomqtt.Client) -> None:
        topic = f"{self.rig.command_topic}/{sf.COMMAND_SUBTOPIC}"
        while True:
            while self._outbox:
                await client.publish(topic, json.dumps(self._outbox[0]), qos=1,
                                     timeout=PUBLISH_TIMEOUT_S)
                self._outbox.popleft()  # only once the broker has it
            self._has_mail.clear()
            await self._has_mail.wait()

    def on_message(self, topic: str, payload: bytes | str, *, retained: bool = False) -> None:
        subtopic = sf.subtopic(topic, self.rig.telemetry_topic)
        if subtopic is None:
            return
        document = sf.parse(payload)
        if subtopic == sf.STATUS_SUBTOPIC:
            self._status(document, retained)
            return
        if not isinstance(document, dict) or not self._of(document.get("gateway")):
            return  # not a gateway this service runs
        if self._repeats.seen(document):
            return  # QoS 1 delivered it twice
        gateway, schema, live = document["gateway"], document.get("schema"), not retained
        if live:
            self._heard(gateway, document.get("boot"))
        if schema == sf.ZONE:
            device = self._zone(gateway, document.get("zone"))
            if device:
                device.zone_update(document, live=live)
        elif schema == sf.HEARTBEAT:
            for device in self._of(gateway):
                device.gateway_heartbeat(document)
        elif schema == sf.DOSE:
            device = self._zone(gateway, document.get("zone"))
            if device:
                device.hal.on_dose_report(document)
        elif schema == sf.CMD_RESULT:
            for device in self._of(gateway):
                device.hal.on_cmd_result(document)

    def _status(self, document: Any, retained: bool) -> None:
        status = sf.status_of(document)
        if status is None or retained:
            return  # a retained status is from before: the next live message says how it is
        gateway, online = status
        if online:
            self._heard(gateway, None)
        else:
            for device in self._of(gateway):
                device.gateway_offline()

    def _heard(self, gateway: str, boot: Any) -> None:
        previous = self._boot.get(gateway)
        rebooted = isinstance(boot, int) and previous is not None and boot != previous
        if isinstance(boot, int):
            self._boot[gateway] = boot
        for device in self._of(gateway):
            device.heard(rebooted=rebooted)

    def _of(self, gateway: Any) -> list[RigZoneDevice]:
        return [device for device in self.devices if device.zone.gateway == gateway]

    def _zone(self, gateway: str, rig_zone: Any) -> RigZoneDevice | None:
        return next((d for d in self._of(gateway) if d.zone.rig_zone == rig_zone), None)
