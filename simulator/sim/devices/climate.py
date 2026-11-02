"""The climate controller (CC1): CLIMATE_TELEMETRY for the polyhouse (contract 6.2)."""

from __future__ import annotations

from typing import Any

from ..farm import ClimateFarm
from ..messages import climate_telemetry, climate_telemetry_topic
from .readings import ReadingDevice


class ClimateDevice(ReadingDevice):
    message_type = "CLIMATE_TELEMETRY"

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        climate: ClimateFarm,
        **options: Any,
    ):
        topic = climate_telemetry_topic(site)
        super().__init__(device_id, kind, site, boot_id, reading_topic=topic, **options)
        self.climate = climate

    def body(self) -> dict[str, Any]:
        return climate_telemetry(self.climate)
