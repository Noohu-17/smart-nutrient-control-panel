"""The HAL of a zone whose controller is the prototype rig (controller/hal.py HAL and TimedPumps).

Sensors are the rig's latest zone update in contract units: EC from TDS, pH, water temperature
and the tank level. Doses are timed: a line's mL become seconds at its pump's flow
(hardware.json), sent as one sf `dose`, and what the pump delivered is measured when the rig
reports how long it really ran (sf/dose/1 "done" or "stopped": elapsed_s x flow). Stopping a
pump early is an sf `switch` OFF. The rig has no flow meter, no circulation pump under its
control and no key switch: the zone is always REMOTE, and no circulation is reported.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import sf
from .config import RigZone

log = logging.getLogger("sim.rig.hal")

FRESH_S = 120.0  # contract 14: EC/pH no older than this to dose on
SENSORS = {"EC": "ec", "PH": "ph", "SOL_TEMP": "solTempC", "RESERVOIR_PCT": "reservoirPct"}


@dataclass
class Run:
    """One timed dose this controller asked the rig for."""

    dose_id: str
    line: str
    relay: str
    state: str = "requested"  # then the rig's: running, done, stopped, rejected
    counted: bool = False


class RigHal:
    timed_pumps = True

    def __init__(
        self,
        zone: RigZone,
        nvs_path: Path,
        send: Callable[[dict[str, Any]], None],
        *,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.zone, self.nvs_path, self._send = zone, nvs_path, send
        self._utc_now, self._monotonic = utc_now, monotonic
        self.latest: dict[str, Any] | None = None  # the zone's newest sf/zone/1
        self._latest_at: float | None = None
        self._runs: dict[str, Run] = {}
        self._on_relay: dict[str, str] = {}  # relay -> the dose id asked of it last
        self._metered = dict.fromkeys(zone.lines, 0.0)  # mL reported, not yet taken
        self._nvs = _read(nvs_path)

    # --- what the rig says ---------------------------------------------------------------

    def update(self, document: dict[str, Any]) -> None:
        """A newer zone update for this zone."""
        self.latest, self._latest_at = document, self._monotonic()

    def on_dose_report(self, document: dict[str, Any]) -> None:
        """sf/dose/1. A dose that ran counts once, at its end, whoever asked for it: a dose from
        an earlier run of this service or from the rig's own panel still went into the water."""
        line = self._line_on(document.get("relay"))
        elapsed, state = document.get("elapsed_s"), document.get("state")
        if line is None or state not in ("running", *sf.FINISHED):
            return
        run = self._runs.get(document.get("dose_id"))
        if run is None:
            run = Run(document.get("dose_id", ""), line, document["relay"])
            if state == "running":
                return
        if state in ("done", "stopped") and not run.counted:
            run.counted = True
            ran_s = elapsed if isinstance(elapsed, (int, float)) and elapsed > 0 else 0.0
            self._metered[line] += ran_s * self.zone.pump_ml_per_s[line]
        run.state = state

    def on_cmd_result(self, document: dict[str, Any]) -> None:
        """sf/cmd-result/1. A dose command the rig didn't take runs nothing; one it already had
        ("duplicate") is still its own, and its sf/dose/1 reports say how it went."""
        run = self._runs.get(document.get("id"))
        refused = document.get("status") not in sf.COMMAND_ACCEPTED
        if run is not None and run.state == "requested" and refused:
            run.state = "rejected"
            log.warning("%s: the rig refused dose %s on %s: %s %s", self.zone.zone_id,
                        run.dose_id, run.relay, document.get("status"), document.get("result"))

    def gateway_restarted(self) -> None:
        """The gateway started again. Zone 2's pumps are its own relays: they came up OFF and the
        doses they ran won't report back. Zone 1's are the actuator box's, which finishes a dose
        on time whatever the link does and reports it once the gateway is back (actuator-node
        main.cpp), so those doses stay open until it does: the controller's restart() switches
        them off and waits for that report, as a stop does."""
        if self.zone.pumps_on_gateway:
            self.forget_runs()

    def forget_runs(self) -> None:
        """The pumps are off, and the doses they were running won't report back."""
        for run in self._runs.values():
            if run.state in ("requested", "running"):
                run.state = "rejected"

    # --- readings, in contract units -----------------------------------------------------

    def readings(
        self, document: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """ZONE_TELEMETRY readings and quality (contract 6.1) from a zone update, the latest
        unless another is given. A reading is STALE once it is older than FRESH_S (by the
        update's own ts, or since it arrived) or its part is older than the rig's own 30 s."""
        document = (self.latest or {}) if document is None else document
        age_s = self._age_s(document)
        water, tank = document.get("water_quality"), document.get("tank")
        scale = 1 / self.zone.tds_ppm_per_ms_cm
        ec = self._reading(water, "tds_ppm", 2, age_s, scale=scale)
        ph = self._reading(water, "ph", 2, age_s, valid=(0, 14))
        temp = self._reading(water, "temp_c", 1, age_s)
        level = (None, "FAULT")
        if isinstance(tank, dict) and tank.get("sensor_ok") is not False:
            level = self._reading(tank, "level_pct", 0, age_s, valid=(0, 100))
        litres = tank.get("liters") if isinstance(tank, dict) else None
        lines = self.zone.lines
        readings = {
            "ec": ec[0],
            "ph": ph[0],
            "solTempC": temp[0],
            "reservoirPct": level[0],
            "reservoirL": round(litres) if level[0] is not None and _number(litres) else None,
            "circFlowLpm": None,  # no circulation flow sensor on the rig: no quality either
            "doMgL": None,
            "dayTankPct": dict.fromkeys(lines),  # the drums have no level sensors
            "dayTankL": dict.fromkeys(lines),
        }
        quality = {"ec": ec[1], "ph": ph[1], "solTempC": temp[1], "reservoirPct": level[1]}
        return readings, quality

    def equipment(self, document: dict[str, Any] | None = None) -> dict[str, str]:
        """The rig's relays as contract equipment: the dosing pumps, and the inlet as REFILL."""
        document = (self.latest or {}) if document is None else document
        actuators, tank = document.get("actuators"), document.get("tank")
        relays = actuators.get("relays") if isinstance(actuators, dict) else None
        equipment = {}
        if isinstance(relays, dict):
            for line, relay in self.zone.pumps.items():
                equipment[f"{self.zone.zone_id}-DOSE-{line}"] = "ON" if relays.get(relay) else "OFF"
        if isinstance(tank, dict) and isinstance(tank.get("inlet_on"), bool):
            equipment[f"{self.zone.zone_id}-REFILL"] = "OPEN" if tank["inlet_on"] else "CLOSED"
        return equipment

    @property
    def estop(self) -> bool:
        return self.estop_in(self.latest or {})

    @staticmethod
    def estop_in(document: dict[str, Any]) -> bool:
        actuators = document.get("actuators")
        return bool(isinstance(actuators, dict) and actuators.get("estop"))

    def _age_s(self, document: dict[str, Any]) -> float:
        ages = []
        if document is self.latest and self._latest_at is not None:
            ages.append(self._monotonic() - self._latest_at)
        if _number(document.get("ts")):
            ages.append(self._utc_now().timestamp() - document["ts"])
        return max(ages) if ages else math.inf

    def _reading(
        self,
        part: Any,
        key: str,
        digits: int,
        age_s: float,
        *,
        scale: float = 1.0,
        valid: tuple[float, float] | None = None,
    ) -> tuple[float | int | None, str]:
        value = part.get(key) if isinstance(part, dict) else None
        if not _number(value):
            return None, "FAULT"  # nothing measured: null with FAULT, never a fake 0
        value = round(value * scale, digits)
        if digits == 0:
            value = int(value)
        if valid and not valid[0] <= value <= valid[1]:
            return value, "OUT_OF_RANGE"
        if age_s > FRESH_S or (part.get("age_s") or 0) > sf.PART_STALE_S:
            return value, "STALE"
        return value, "OK"

    # --- the HAL --------------------------------------------------------------------------

    def read_sensor(self, sensor_id: str) -> tuple[float | None, str]:
        name = SENSORS.get(sensor_id)
        if name is None:
            return None, "FAULT"  # no such sensor on the rig: nothing to measure
        readings, quality = self.readings()
        return readings[name], quality[name]

    def read_input(self, input_id: str) -> bool:
        return {"ESTOP": self.estop, "MODE_REMOTE": True}.get(input_id, False)

    def set_output(self, output_id: str, on: bool) -> None:
        if output_id == "CIRC_PUMP":
            return  # the rig's circulation isn't switched by the controller
        if not output_id.startswith("PUMP_"):
            if on:
                raise ValueError(f"{self.zone.zone_id}: the rig has no {output_id} to switch on")
            return
        if on:
            raise ValueError("a timed pump runs through run_pump_for, never switched on")
        relay = self.zone.pumps[output_id.removeprefix("PUMP_")]
        if self.pump_running(output_id):
            self._send(sf.switch_off_command(command_id=uuid.uuid4().hex, target=self.zone.target,
                                             relays=[relay], issued_at=self._unix_now()))

    def run_pump_for(self, output_id: str, ml: float, run_id: str) -> None:
        line = output_id.removeprefix("PUMP_")
        relay, seconds = self.zone.pumps[line], ml / self.zone.pump_ml_per_s[line]
        run = Run(sf.dose_id_for(run_id), line, relay)
        self._runs[run.dose_id], self._on_relay[relay] = run, run.dose_id
        low, high = sf.DOSE_SECONDS
        if not low <= seconds <= high:
            run.state = "rejected"  # the rig takes 0.1-600 s: nothing to send
            log.warning("%s: %.1f mL of %s is %.1f s of pumping, outside the rig's %g-%g s",
                        self.zone.zone_id, ml, line, seconds, low, high)
            return
        self._send(sf.dose_command(dose_id=run.dose_id, target=self.zone.target, relay=relay,
                                   seconds=seconds, issued_at=self._unix_now()))

    def pump_running(self, output_id: str) -> bool:
        relay = self.zone.pumps.get(output_id.removeprefix("PUMP_"))
        run = self._runs.get(self._on_relay.get(relay, ""))
        return run is not None and run.state in ("requested", "running")

    def take_flow_ml(self, meter_id: str) -> float:
        line = meter_id.removeprefix("FLOW_")
        counted = self._metered.get(line, 0.0)
        self._metered[line] = 0.0
        return counted

    def now_ms(self) -> int:
        """Real time: a live zone runs on the clock on the wall."""
        return self.utc_ms()

    def utc_ms(self) -> int:
        return round(self._utc_now().timestamp() * 1000)

    def nvs_read(self, key: str) -> dict | None:
        value = self._nvs.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def nvs_write(self, key: str, value: dict) -> None:
        """Written to disk at once, and never half-written."""
        self._nvs[key] = json.loads(json.dumps(value))
        self.nvs_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.nvs_path.with_name(self.nvs_path.name + ".tmp")
        temp.write_text(json.dumps(self._nvs, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.nvs_path)

    def _line_on(self, relay: Any) -> str | None:
        return next((line for line, r in self.zone.pumps.items() if r == relay), None)

    def _unix_now(self) -> int:
        return int(self._utc_now().timestamp())


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); the controller starts with empty nvs", path, err)
        return {}
    return data if isinstance(data, dict) else {}
