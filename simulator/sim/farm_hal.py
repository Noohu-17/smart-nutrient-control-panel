"""The HAL a zone controller gets in the simulator: its zone of the farm model (contract 9).

The controller can't tell it from hardware. Sensors read what telemetry reports, outputs switch the
zone's equipment, the E-STOP and key switch are the panel's, and each dosing line's flow meter
counts what its pump really delivered. nvs is a JSON file in the state folder, so what a controller
keeps (journal, last 50 cmdIds, latches, 24 h totals) survives a restart of the simulator.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .farm import Farm, ZoneFarm
from .messages import zone_telemetry

log = logging.getLogger("sim.hal")

SENSOR_READINGS = {  # HAL sensor ID: the contract 6.1 reading it is
    "EC": "ec",
    "PH": "ph",
    "SOL_TEMP": "solTempC",
    "RESERVOIR_PCT": "reservoirPct",
    "CIRC_FLOW": "circFlowLpm",
}


class FarmHal:
    def __init__(
        self,
        farm: Farm,
        zone: ZoneFarm,
        nvs_path: Path,
        *,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.farm, self.zone, self.nvs_path, self._utc_now = farm, zone, nvs_path, utc_now
        self._nvs = _read(nvs_path)

    def read_sensor(self, sensor_id: str) -> tuple[float | None, str]:
        """(value, quality), as the zone's telemetry would report it now."""
        telemetry = zone_telemetry(self.zone)
        readings, quality = telemetry["readings"], telemetry["quality"]
        name = SENSOR_READINGS.get(sensor_id)
        if name is not None:
            value = readings[name]
            return value, quality.get(name, "OK" if value is not None else "FAULT")
        if sensor_id.startswith("DAYTANK_") and sensor_id.endswith("_PCT"):
            value = readings["dayTankPct"].get(sensor_id[len("DAYTANK_"):-len("_PCT")])
            return value, "OK" if value is not None else "FAULT"
        return None, "FAULT"  # no such sensor: nothing to measure

    def read_input(self, input_id: str) -> bool:
        zone = self.zone
        inputs = {
            "ESTOP": zone.estop,
            "LEAK": zone.leak,
            "MODE_REMOTE": zone.mode == "REMOTE",
            "MODE_MAINT": zone.mode == "MAINT",
        }
        return inputs.get(input_id, False)

    def set_output(self, output_id: str, on: bool) -> None:
        zone_id = self.zone.zone_id
        if output_id == "CIRC_PUMP":
            equipment, state = f"{zone_id}-CIRC", "ON" if on else "OFF"
        elif output_id == "REFILL_VALVE":
            equipment, state = f"{zone_id}-REFILL", "OPEN" if on else "CLOSED"
        else:
            equipment, state = f"{zone_id}-DOSE-{output_id.removeprefix('PUMP_')}", (
                "ON" if on else "OFF"
            )
        if equipment not in self.zone.outputs:
            raise ValueError(f"{zone_id} has no output {output_id}")
        self.zone.outputs[equipment] = state

    def take_flow_ml(self, meter_id: str) -> float:
        """What the line's flow meter counted since the last call (meter FLOW_<line>)."""
        line = meter_id.removeprefix("FLOW_")
        counted = self.zone.flow_meter_ml.get(line, 0.0)
        self.zone.flow_meter_ml[line] = 0.0
        return counted

    def sim_now(self) -> datetime:
        """The farm's time: simulated time, wherever the clock task has moved it to."""
        if self.farm.now is None:
            raise ValueError("the farm has no time yet: set farm.now before the controllers run")
        return self.farm.now

    def now_ms(self) -> int:
        return round(self.sim_now().timestamp() * 1000)

    def utc_ms(self) -> int:
        return round(self._utc_now().timestamp() * 1000)

    def nvs_read(self, key: str) -> dict | None:
        value = self._nvs.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def nvs_write(self, key: str, value: dict) -> None:
        """Written to disk at once, and never half-written, like flash."""
        self._nvs[key] = json.loads(json.dumps(value))
        self.nvs_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.nvs_path.with_name(self.nvs_path.name + ".tmp")
        temp.write_text(json.dumps(self._nvs, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.nvs_path)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); the controller starts with empty nvs", path, err)
        return {}
    if not isinstance(data, dict):
        log.warning("%s is not a JSON object; the controller starts with empty nvs", path)
        return {}
    return data
