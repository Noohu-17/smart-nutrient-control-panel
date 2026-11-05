"""A zone controller (ZC1, ZC2 ...): ZONE_TELEMETRY and ZONE_STATE for its zone (6.1, 6.8), and,
with its controller attached, the commands for it (contract 4, 7, 9).

The controller (sim/controller, T3-4) decides; this device carries messages. It hands every command
from the zone's cmd topic and farm/{site}/stop to controller.handle(), and puts what the controller
reports on the contract topics: CMD_STATE and EVENT at QoS 1, not retained. ZONE_STATE goes through
StateDevice, so it is one message whoever noticed the change.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..farm import ZoneFarm
from ..messages import (
    stop_topic,
    zone_cmd_state_topic,
    zone_cmd_topic,
    zone_event_topic,
    zone_state,
    zone_state_topic,
    zone_telemetry,
    zone_telemetry_topic,
)
from .readings import StateDevice

if TYPE_CHECKING:
    from ..controller.zone import ZoneController

log = logging.getLogger("sim.devices")


class ZoneDevice(StateDevice):
    message_type = "ZONE_TELEMETRY"
    state_type = "ZONE_STATE"
    state_with_readings = True  # contract 6.8: "on every change (and with each telemetry message)"

    def __init__(
        self, device_id: str, kind: str, site: str, boot_id: int, *, zone: ZoneFarm, **options: Any
    ):
        super().__init__(
            device_id,
            kind,
            site,
            boot_id,
            reading_topic=zone_telemetry_topic(site, zone.zone_id),
            state_topic=zone_state_topic(site, zone.zone_id),
            **options,
        )
        self.zone = zone
        self.controller: ZoneController | None = None
        self.sim_now: Callable[[], datetime] | None = None  # the farm's time, for simTs
        self.cmd_topic = zone_cmd_topic(site, zone.zone_id)
        self.stop_topic = stop_topic(site)
        self.cmd_state_topic = zone_cmd_state_topic(site, zone.zone_id)
        self.event_topic = zone_event_topic(site, zone.zone_id)

    def body(self) -> dict[str, Any]:
        return zone_telemetry(self.zone)

    def state_body(self) -> dict[str, Any]:
        return zone_state(self.zone)

    def subscriptions(self) -> tuple[str, ...]:
        """Its zone's commands and every stop (contract 4); nothing without a controller."""
        return (self.cmd_topic, self.stop_topic) if self.controller else ()

    def on_message(self, topic: str, payload: bytes | str) -> None:
        try:
            cmd = json.loads(payload)
        except ValueError:
            log.warning("%s dropped a message on %s that is not JSON", self.id, topic)
            return
        if not isinstance(cmd, dict):
            log.warning("%s dropped a message on %s that is not a JSON object", self.id, topic)
            return
        try:
            self.controller.handle(cmd)
        except Exception:  # one bad command must not take the device off the broker
            log.exception("%s could not handle a command on %s", self.id, topic)

    def report(self, msg_type: str, body: dict[str, Any]) -> None:
        """What the controller says, onto the contract topics (contract 4, 9 rule 6)."""
        sim_ts = self.sim_now() if self.sim_now else self.current_sim_ts()
        if msg_type == "ZONE_STATE":
            zone = self.zone  # the controller's own fields; outputs, mode and E-STOP are the farm's
            zone.stopped, zone.operation = body["stopped"], body["operation"]
            zone.latches, zone.config_version = set(body["latches"]), body["configVersion"]
            self.publish_state_if_changed(sim_ts)
            return
        topic = self.cmd_state_topic if msg_type == "CMD_STATE" else self.event_topic
        self._send_message(topic, msg_type, body, sim_ts, qos=1, retain=False)
