"""The climate controller (CC1): CLIMATE_TELEMETRY and CLIMATE_STATE (contract 6.2, 6.9)."""

from __future__ import annotations

from typing import Any

from ..farm import ClimateFarm
from ..messages import (
    climate_state,
    climate_state_topic,
    climate_telemetry,
    climate_telemetry_topic,
)
from .readings import StateDevice


class ClimateDevice(StateDevice):
    message_type = "CLIMATE_TELEMETRY"
    state_type = "CLIMATE_STATE"

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
        super().__init__(
            device_id,
            kind,
            site,
            boot_id,
            reading_topic=climate_telemetry_topic(site),
            state_topic=climate_state_topic(site),
            **options,
        )
        self.climate = climate

    def body(self) -> dict[str, Any]:
        return climate_telemetry(self.climate)

    def state_body(self) -> dict[str, Any]:
        return climate_state(self.climate)
