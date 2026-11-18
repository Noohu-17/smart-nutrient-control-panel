"""A zone controller (ZC1 ...) as this service plays it for a zone the prototype rig runs.

It speaks the contract exactly as a simulated zone controller does (contract 4-9): the controller
is sim/controller's, on the rig's HAL. What it reports comes from the rig: ZONE_TELEMETRY from each
zone update, ZONE_STATE with the rig's real relays, DEVICE_STATUS with the gateway's firmware,
uptime and 4G signal.

It is online only while the rig is: silent until the rig is heard, and when the rig goes quiet
(its last will, or no message for RIG_OFFLINE_S) its link drops without a goodbye, so the broker
publishes ZC1's own last will (DEVICE_STATUS offline, 6.4). Commands for it then wait at the
broker. When the rig is back, ZC1 is back with the same boot, and the controller waits a moment
for the rig's queued reports before it judges anything. A rig that restarted is a new boot:
pumps off, the zone stopped, an interrupted dose closed (restart(), 6.8), on Zone 1 once the
actuator box has reported what it ran.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..devices.base import drop_link
from ..devices.readings import StateDevice
from ..messages import (
    Envelope,
    stop_topic,
    zone_cmd_state_topic,
    zone_cmd_topic,
    zone_event_topic,
    zone_state_topic,
    zone_telemetry_topic,
)
from .config import RigZone
from .hal import RigHal

if TYPE_CHECKING:
    from ..controller.zone import ZoneController

log = logging.getLogger("sim.rig")

RESUME_GRACE_S = 10.0  # after the rig is back, its queued reports come first


class RigZoneDevice(StateDevice):
    message_type = "ZONE_TELEMETRY"
    state_type = "ZONE_STATE"
    state_with_readings = True

    def __init__(
        self,
        zone: RigZone,
        site: str,
        boot_id: int,
        hal: RigHal,
        *,
        offline_after_s: float,
        next_boot_id: Callable[[], int],
        resume_grace_s: float = RESUME_GRACE_S,
        **options: Any,
    ):
        super().__init__(
            zone.device_id,
            "ZONE_CONTROLLER",
            site,
            boot_id,
            reading_topic=zone_telemetry_topic(site, zone.zone_id),
            state_topic=zone_state_topic(site, zone.zone_id),
            **options,
        )
        self.zone, self.hal = zone, hal
        self.controller: ZoneController | None = None
        self.heartbeat: dict[str, Any] | None = None  # the gateway's latest sf/heartbeat/1
        self.cmd_topic = zone_cmd_topic(site, zone.zone_id)
        self.cmd_state_topic = zone_cmd_state_topic(site, zone.zone_id)
        self.event_topic = zone_event_topic(site, zone.zone_id)
        self.stop_topic = stop_topic(site)
        self._offline_after_s, self._grace_s = offline_after_s, resume_grace_s
        self._next_boot_id = next_boot_id
        self._controller_state: dict[str, Any] | None = None
        self._contact_at: float | None = None
        self._step_from = 0.0
        # The rig can be away, not ZC1's memory: what it says meanwhile waits in the outbox.
        self.online = False  # silent until the rig is heard
        self._power_on.clear()

    # --- the rig ---------------------------------------------------------------------------

    def heard(self, *, rebooted: bool) -> None:
        """A live message from this zone's gateway (not a retained one from before)."""
        now = self._monotonic()
        self._contact_at = now
        if rebooted:
            self._restarted(now)
        elif not self.online:
            log.info("%s: the rig is back", self.id)
            self._back(now)

    def zone_update(self, document: dict[str, Any], *, live: bool) -> None:
        """sf/zone/1 for this zone: the newest becomes the HAL's, and each one sent while online
        becomes ZONE_TELEMETRY at its own time, so a backlog fills in the history."""
        newest = self._is_newest(document)
        if newest:
            self.hal.update(document)
        if not self.online or not live:
            return
        at = _time_of(document) or self._now()
        if newest:
            self.publish_state_if_changed(None)
        self._send_at(self.reading_topic, self.message_type, self._telemetry(document), at)

    def gateway_heartbeat(self, document: dict[str, Any]) -> None:
        self.heartbeat = document

    def gateway_offline(self) -> None:
        """The gateway's last will: offline at once."""
        if self.online:
            self._lost("the rig's last will")

    def tick(self) -> None:
        """The service's fixed-rate step: notice silence, and step the controller."""
        now = self._monotonic()
        if self.online and now - (self._contact_at or now) > self._offline_after_s:
            self._lost(f"no message for {self._offline_after_s:g} s")
        if self.online and self.controller and now >= self._step_from:
            self.controller.step(self.hal.now_ms())

    def _lost(self, why: str) -> None:
        log.warning("%s: the rig is offline (%s): %s goes silent", self.zone.zone_id, why, self.id)
        self.online = False
        self._power_on.clear()
        if self._client is not None:
            drop_link(self._client)  # no goodbye: the broker publishes ZC1's last will

    def _back(self, now: float) -> None:
        self.online = True
        self._step_from = now + self._grace_s
        self.send(self.topic, self.status, qos=1, retain=True)
        self._status_queued = True
        self._report_now()
        self._power_on.set()

    def _restarted(self, now: float) -> None:
        """The rig started again: a new boot for ZC1, with the controller's restart."""
        boot_id = self._next_boot_id()
        log.warning("%s: the rig restarted: %s starts boot %d", self.zone.zone_id, self.id, boot_id)
        self.envelope = Envelope(self.site, self.id, boot_id)
        self._booted = now
        was_online, self.online = self.online, True
        self.send(self.topic, self.status, qos=1, retain=True)
        self._status_queued = True
        self.hal.gateway_restarted()  # before restart(): it decides what restart() switches off
        if self.controller:
            self.controller.restart("REBOOT")
        self._report_now()
        self._step_from = now + self._grace_s
        if not was_online:
            self._power_on.set()

    def _report_now(self) -> None:
        """Its state, and its readings once the rig has sent any: never a reading of nothing."""
        if self.hal.latest is not None:
            self.take_reading(None)
        else:
            self.publish_state(None)

    # --- what it says ----------------------------------------------------------------------

    def body(self) -> dict[str, Any]:
        return self._telemetry(self.hal.latest or {})

    def state_body(self) -> dict[str, Any]:
        """The controller's ZONE_STATE, with the rig's real relays and E-STOP."""
        state = dict(self._controller_state or {"zone": self.zone.zone_id, "mode": "REMOTE",
                                                 "stopped": False, "operation": None,
                                                 "latches": [], "configVersion": None})
        state["estop"] = self.hal.estop
        state["outputs"] = self.hal.equipment()
        return state

    def status(self) -> dict[str, Any]:
        """DEVICE_STATUS (6.4) from the gateway's heartbeat: the hardware's own numbers."""
        beat = self.heartbeat or {}
        fw, link, memory = (beat.get(key) or {} for key in ("fw", "link", "memory"))
        heap = memory.get("heap_free")
        uptime = beat.get("uptime_s")
        return {
            **self.envelope.next("DEVICE_STATUS", self._now()),
            "online": True,
            "fw": str(fw.get("version") or "unknown"),
            "fwHash": fw.get("rev") if isinstance(fw.get("rev"), str) else None,
            "uptimeS": uptime if isinstance(uptime, int) and uptime >= 0 else 0,
            "rssiDbm": link.get("signal_dbm") if isinstance(link.get("signal_dbm"), int) else None,
            "clockSynced": isinstance(beat.get("ts"), int),
            "configVersion": None,
            "freeHeapKb": heap // 1024 if isinstance(heap, int) and heap >= 0 else None,
        }

    def report(self, msg_type: str, body: dict[str, Any]) -> None:
        """What the controller says, onto the contract topics (contract 4, 9 rule 6)."""
        if msg_type == "ZONE_STATE":
            self._controller_state = body
            self.publish_state_if_changed(None)
            return
        topic = self.cmd_state_topic if msg_type == "CMD_STATE" else self.event_topic
        self._send_message(topic, msg_type, body, None, qos=1, retain=False)

    def subscriptions(self) -> tuple[str, ...]:
        return (self.cmd_topic, self.stop_topic)

    def on_message(self, topic: str, payload: bytes | str) -> None:
        try:
            cmd = json.loads(payload)
        except ValueError:
            log.warning("%s dropped a message on %s that is not JSON", self.id, topic)
            return
        if not isinstance(cmd, dict) or self.controller is None:
            log.warning("%s dropped a message on %s that is not a command", self.id, topic)
            return
        try:
            self.controller.handle(cmd)
        except Exception:  # one bad command must not take the zone off the broker
            log.exception("%s could not handle a command on %s", self.id, topic)

    def _telemetry(self, document: dict[str, Any]) -> dict[str, Any]:
        readings, quality = self.hal.readings(document)
        operation = (self._controller_state or {}).get("operation")
        return {
            "zone": self.zone.zone_id,
            "readings": readings,
            "quality": quality,
            "equipment": self.hal.equipment(document),
            "mode": "REMOTE",  # the rig has no key switch
            "estop": self.hal.estop_in(document),
            "leak": False,  # and no leak sensor
            "activeCmdId": operation["cmdId"] if operation else None,
        }

    def _send_at(self, topic: str, msg_type: str, body: dict[str, Any], at: datetime) -> None:
        def build() -> dict[str, Any]:
            return {**self.envelope.next(msg_type, at), **body}

        self.send(topic, build, qos=self.qos, retain=self.retain)

    def _is_newest(self, document: dict[str, Any]) -> bool:
        latest = self.hal.latest
        if latest is None:
            return True
        return (document.get("boot", 0), document.get("seq", 0)) > (
            latest.get("boot", 0), latest.get("seq", 0))


def _time_of(document: dict[str, Any]) -> datetime | None:
    ts = document.get("ts")
    if isinstance(ts, int) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts, UTC)
    return None
