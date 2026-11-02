"""A zone controller (ZC1, ZC2 ...): ZONE_TELEMETRY for its zone (contract 6.1)."""

from __future__ import annotations

from typing import Any

from ..farm import ZoneFarm
from ..messages import zone_telemetry, zone_telemetry_topic
from .readings import ReadingDevice


class ZoneDevice(ReadingDevice):
    message_type = "ZONE_TELEMETRY"

    def __init__(
        self, device_id: str, kind: str, site: str, boot_id: int, *, zone: ZoneFarm, **options: Any
    ):
        topic = zone_telemetry_topic(site, zone.zone_id)
        super().__init__(device_id, kind, site, boot_id, reading_topic=topic, **options)
        self.zone = zone

    def body(self) -> dict[str, Any]:
        return zone_telemetry(self.zone)
