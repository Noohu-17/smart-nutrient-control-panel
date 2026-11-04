"""A zone controller (ZC1, ZC2 ...): ZONE_TELEMETRY and ZONE_STATE for its zone (6.1, 6.8)."""

from __future__ import annotations

from typing import Any

from ..farm import ZoneFarm
from ..messages import zone_state, zone_state_topic, zone_telemetry, zone_telemetry_topic
from .readings import StateDevice


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

    def body(self) -> dict[str, Any]:
        return zone_telemetry(self.zone)

    def state_body(self) -> dict[str, Any]:
        return zone_state(self.zone)
