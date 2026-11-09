"""The playground's farm side (contract 11.4): speed, reset, held values and failure drills.

The control API (control_api.py) asks it for each change, and the clock task moves the farm
through step(), so a held value stays where it was put. A held value changes the simulated water
or air itself, so the controllers' own checks react to it (11.4.2); a drill breaks one part until
Fix repairs it (11.4.3). Every request is checked against the config pack first: speeds in
simulator.json, holdable ranges and the drill catalogue in scenarios.json, targets in layout.json.
Nothing here sends a message: people see the results in the normal contract messages.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .clock import SimClock, reading_interval_s
from .config import load_demo_start, read_pack_file
from .farm import Farm, ZoneFarm, load_farm
from .messages import utc_ts, zone_telemetry
from .model.nutrients import nutrient_g_for_ec

POLYHOUSE = "POLYHOUSE"  # the target of a held air value and of a POLYHOUSE drill
ZONE_PROBES = ("ec", "ph", "solTempC")  # what a ZONE_SENSOR drill aims at, as the app (T2-18)
GRADUAL = ("ec", "ph", "solTempC", "airTempC")  # 11.4.2: these move as real water and air would
GRADUAL_READINGS = 2  # ... over about two readings
ZONE_METRICS = ("ec", "ph", "solTempC", "circFlowLpm", "reservoirPct", "dayTankPct")  # settable
AIR_METRICS = ("airTempC", "airRh")
BUILT_DRILLS = (  # the rest of the catalogue is refused until it is built
    "SENSOR_OFFLINE", "STUCK_READING", "ESTOP", "DEVICE_OFFLINE",
)


class PlaygroundError(Exception):
    """A request refused: its HTTP status and a contract 11.1 error body."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field

    def body(self) -> dict[str, Any]:
        details = {"fields": [{"field": self.field, "message": self.message}]} if self.field else {}
        return {"error": {"code": self.code, "message": self.message, "details": details}}


def invalid(field: str, message: str) -> PlaygroundError:
    return PlaygroundError(422, "VALIDATION_ERROR", message, field)


class Controller(Protocol):
    def reset_to_start(self) -> None: ...


class Device(Protocol):
    """A simulated device a DEVICE_OFFLINE drill stops and starts (devices/base.py)."""

    id: str
    powered: bool

    def go_offline(self) -> None: ...

    def come_back(self, boot_id: int) -> None: ...


@dataclass
class Hold:
    target: str
    metric: str
    value: float
    gradual: bool
    start_value: float | None = None  # where a gradual change started, and when
    start_at: datetime | None = None
    ramp_s: float = 0.0


class Playground:
    def __init__(
        self,
        farm: Farm,
        clock: SimClock,
        client_dir: Path,
        *,
        controllers: Iterable[Controller] = (),
        devices: Iterable[Device] = (),
        device_ids: Iterable[str] = (),
        min_real_reading_s: float = 0.0,
        next_boot_id: Callable[[str], int] | None = None,
    ):
        """devices: what DEVICE_OFFLINE stops and starts; device_ids, without them, only names
        the targets. next_boot_id gives a restarting device its new bootId (state.py)."""
        self.farm, self.clock, self.client_dir = farm, clock, client_dir
        self.controllers = tuple(controllers)
        self.devices = {device.id: device for device in devices}
        self.device_ids = tuple(device_ids) or tuple(self.devices)
        self._next_boot_id = next_boot_id
        self.start_speed = clock.speed
        self._min_real_reading_s = min_real_reading_s
        scenarios = read_pack_file(client_dir, "scenarios.json")
        self.holdable = scenarios.get("holdable") or {}
        self.drills = {
            drill["kind"]: drill
            for drill in scenarios.get("failureDrills") or ()
            if isinstance(drill, Mapping) and "kind" in drill
        }
        start = load_demo_start(client_dir)
        self.scenario, self.weather = start["scenario"], start["weather"]
        self.holds: dict[tuple[str, str], Hold] = {}
        self.faults: dict[tuple[str, str], None] = {}  # (kind, target), in the order triggered
        self._normal = self._normal_of(farm)

    # --- GET /state, POST /speed, POST /reset ----------------------------------------------

    def state(self) -> dict[str, Any]:
        """Field names as the sim_state message (contract 12) and the app (T2-18)."""
        speed = self.clock.speed
        return {
            "scenario": self.scenario,
            "simTime": utc_ts(self.clock.now()),
            "speed": int(speed) if float(speed).is_integer() else speed,  # 60, not 60.0
            "weather": self.weather,
            "faults": [{"kind": kind, "target": target} for kind, target in self.faults],
            "holds": [
                {"target": h.target, "metric": h.metric, "value": h.value, "gradual": h.gradual}
                for h in self.holds.values()
            ],
        }

    def set_speed(self, body: Any) -> None:
        x = _object(body).get("x")
        allowed = self.clock.allowed_speeds
        if isinstance(x, bool) or x not in allowed:
            raise invalid("body.x", f"Speed must be one of {', '.join(f'{s:g}' for s in allowed)}")
        self.clock.set_speed(x)

    def reset(self) -> None:
        """Back to the demo starting state: drills off, holds released, levels and readings as
        at the start, each controller as a fresh one (anything running ends STOPPED, nothing
        stopped, no 24 h totals) and the starting speed. Simulated time carries on: simTs never
        goes back, so history stays in order."""
        for kind, target in list(self.faults):
            self._drill(kind, target, False)
        self.faults.clear()
        for target, metric in list(self.holds):
            self.release(target, metric)
        for controller in self.controllers:
            controller.reset_to_start()
        fresh = load_farm(self.client_dir)
        for zone_id, zone in self.farm.zones.items():
            start = fresh.zones[zone_id]
            zone.reservoir = start.reservoir
            zone.day_tank_l.update(start.day_tank_l)
            zone.pump_flow_lpm = start.pump_flow_lpm
            zone.flow_meter_ml.update(dict.fromkeys(zone.flow_meter_ml, 0.0))
            zone.unmixed_ml.clear()
            zone.mode, zone.leak = start.mode, start.leak
        self.farm.climate.air = fresh.climate.air
        for item in dataclasses.fields(self.farm.power):
            setattr(self.farm.power, item.name, getattr(fresh.power, item.name))
        self._normal = self._normal_of(fresh)
        self.clock.set_speed(self.start_speed)

    # --- held values (11.4.2) -------------------------------------------------------------

    def hold(self, body: Any) -> None:
        body = _object(body)
        target, metric, value = body.get("target"), body.get("metric"), body.get("value")
        gradual = body.get("gradual", True)
        low, high = self._range(target, metric)
        if not _is_number(value) or not low <= value <= high:
            raise invalid("body.value", f"{metric} must be a number from {low:g} to {high:g}")
        if not isinstance(gradual, bool):
            raise invalid("body.gradual", "gradual must be true or false")
        start = self._current(target, metric)  # before a release puts flow or air back to normal
        self.release(target, metric)
        hold = Hold(target, metric, value, gradual)
        if gradual and metric in GRADUAL and start is not None and self.farm.now is not None:
            interval_s = reading_interval_s(self.clock.speed, self._min_real_reading_s)
            hold.start_value, hold.start_at = start, self.farm.now
            hold.ramp_s = GRADUAL_READINGS * interval_s
        self.holds[(target, metric)] = hold
        self._apply(hold, self.farm.now)

    def release(self, target: Any, metric: Any) -> bool:
        """False when nothing was held there. The water keeps its value and drifts on; flow and
        air, which have no model to move them back, return to normal."""
        if self.holds.pop((target, metric), None) is None:
            return False
        if metric == "circFlowLpm":
            self.farm.zones[target].pump_flow_lpm = self._normal["flow"][target]
        elif target == POLYHOUSE:
            normal, air = self._normal["air"], self.farm.climate.air
            if metric == "airTempC":
                self.farm.climate.air = dataclasses.replace(air, temp_by_pos_c=normal.temp_by_pos_c)
            else:
                self.farm.climate.air = dataclasses.replace(air, rh=normal.rh)
        return True

    def release_all(self) -> None:
        for target, metric in list(self.holds):
            self.release(target, metric)

    # --- failure drills (11.4.3) ------------------------------------------------------------

    def fault(self, body: Any) -> None:
        """Trigger ("on": true) or Fix ("on": false) a drill from the scenarios.json catalogue."""
        body = _object(body)
        kind, target, on = body.get("kind"), body.get("target"), body.get("on")
        drill = self.drills.get(kind) if isinstance(kind, str) else None
        if drill is None:
            raise invalid("body.kind", f"There is no failure drill {kind!r}")
        if target not in self.drill_targets(drill):
            raise invalid("body.target", f"{drill.get('title', kind)} can't be aimed at {target!r}")
        if not isinstance(on, bool):
            raise invalid("body.on", "on must be true (Trigger) or false (Fix)")
        if kind not in BUILT_DRILLS:
            title = drill.get("title", kind)
            raise invalid("body.kind", f"{title} isn't built in the simulator yet")
        if kind == "DEVICE_OFFLINE" and (target not in self.devices or not self._next_boot_id):
            raise invalid("body.target", f"{target} can't be switched off in this run")
        self._drill(kind, target, on)
        if on:
            self.faults[(kind, target)] = None
        else:
            self.faults.pop((kind, target), None)

    def drill_targets(self, drill: Mapping[str, Any]) -> list[str]:
        """What a drill can be aimed at on this farm; zone parts are "Z1.ph" and "Z1.ACID"."""
        zones = self.farm.zones.values()
        match drill.get("targetType"):
            case "ZONE":
                return [zone.zone_id for zone in zones]
            case "ZONE_SENSOR":
                return [
                    f"{zone.zone_id}.{probe}"
                    for zone in zones
                    for probe in (*ZONE_PROBES, *(("doMgL",) if zone.do_fitted else ()))
                ]
            case "DOSE_LINE":
                return [f"{zone.zone_id}.{line}" for zone in zones for line in zone.day_tank_l]
            case "POLYHOUSE":
                return [POLYHOUSE]
            case "DEVICE":
                return list(self.device_ids)
        return []

    def _drill(self, kind: str, target: str, on: bool) -> None:
        zone_id, _, part = target.partition(".")
        if kind == "SENSOR_OFFLINE":
            faults = self.farm.zones[zone_id].sensor_faults
            if on:
                faults.add(part)
            else:
                faults.discard(part)
        elif kind == "STUCK_READING":
            stuck = self.farm.zones[zone_id].stuck_readings
            if not on:
                stuck.pop(part, None)
            elif part not in stuck:  # it sticks at what it reads now; Trigger again keeps that
                stuck[part] = zone_telemetry(self.farm.zones[zone_id])["readings"][part]
        elif kind == "ESTOP":
            # The panel's E-STOP input: the controller notices it on its next step, as it
            # would a real one, so pressing ends a dose STOPPED and releasing restarts nothing.
            self.farm.zones[zone_id].estop = on
        elif kind == "DEVICE_OFFLINE":
            # The device stops: silent, last will, outputs off. Fix starts it again as a new
            # boot; a zone controller comes back stopped (devices/base.py, devices/zone.py).
            device = self.devices[target]
            if on:
                device.go_offline()
            elif not device.powered:
                device.come_back(self._next_boot_id(target))

    # --- the clock task's step -------------------------------------------------------------

    def step(self, dt_s: float, now: datetime) -> None:
        """The farm moves, people's actions release what they act on, and holds stay put."""
        self.farm.step(dt_s, now)
        for zone in self.farm.zones.values():
            for metric in _acted_on(zone):
                self.release(zone.zone_id, metric)
        for hold in self.holds.values():
            self._apply(hold, now)

    def _apply(self, hold: Hold, now: datetime | None) -> None:
        value = hold.value
        if hold.ramp_s > 0 and hold.start_value is not None and now is not None:
            done = (now - hold.start_at).total_seconds() / hold.ramp_s
            if done < 1:
                value = hold.start_value + (hold.value - hold.start_value) * max(done, 0.0)
        if hold.target == POLYHOUSE:
            self._set_air(hold.metric, value)
        else:
            _set_water(self.farm.zones[hold.target], hold.metric, value)

    def _set_air(self, metric: str, value: float) -> None:
        """No air model yet, so a held value sets the air; once one exists it becomes a load."""
        air = self.farm.climate.air
        if metric == "airTempC":
            shift = value - air.temp_c
            temps = {pos: t + shift for pos, t in air.temp_by_pos_c.items()}
            self.farm.climate.air = dataclasses.replace(air, temp_by_pos_c=temps)
        else:
            self.farm.climate.air = dataclasses.replace(air, rh=value)

    # --- what can be held, and its value now -----------------------------------------------

    def _range(self, target: Any, metric: Any) -> tuple[float, float]:
        """The allowed range from scenarios.json holdable; dayTankPct is one metric per line."""
        base, _, line = metric.partition(".") if isinstance(metric, str) else ("", "", "")
        if target == POLYHOUSE:
            spec = (self.holdable.get("polyhouse") or {}).get(metric)
            settable = base in AIR_METRICS
        elif isinstance(target, str) and target in self.farm.zones:
            spec = (self.holdable.get("zone") or {}).get(base)
            if isinstance(spec, Mapping) and spec.get("linesFrom"):  # dayTankPct.A, .B, .ACID
                if line not in self.farm.zones[target].day_tank_l:
                    spec = None
            elif line:
                spec = None
            settable = base in ZONE_METRICS
        else:
            raise invalid("body.target", f"There is no {target!r} to hold: a zone or {POLYHOUSE}")
        if not isinstance(spec, Mapping):
            raise invalid("body.metric", f"{metric!r} can't be held on {target}")
        if not settable:
            raise invalid("body.metric", f"{metric} can't be held in the simulator yet")
        return spec["min"], spec["max"]

    def _current(self, target: str, metric: str) -> float | None:
        if target == POLYHOUSE:
            air = self.farm.climate.air
            return air.temp_c if metric == "airTempC" else air.rh
        water = self.farm.zones[target].water()
        return {"ec": water.ec_ms_cm, "ph": water.ph, "solTempC": water.temp_c}.get(metric)

    @staticmethod
    def _normal_of(farm: Farm) -> dict[str, Any]:
        """What a released flow or air hold goes back to: the farm as configured."""
        return {
            "flow": {zone_id: zone.pump_flow_lpm for zone_id, zone in farm.zones.items()},
            "air": farm.climate.air,
        }


def _set_water(zone: ZoneFarm, metric: str, value: float) -> None:
    """Change the zone's water or equipment itself, so the controller's checks see it too."""
    water, params = zone.reservoir, zone.params
    if metric == "reservoirPct":
        volume_l = params.capacity_l * value / 100.0
        if water.volume_l > 0:  # the level changes, the solution stays the same strength
            nutrient_g = water.nutrient_g * volume_l / water.volume_l
            zone.reservoir = dataclasses.replace(water, volume_l=volume_l, nutrient_g=nutrient_g)
        else:  # an empty tank fills with raw water
            raw = params.raw_water
            zone.reservoir = dataclasses.replace(
                water,
                volume_l=volume_l,
                nutrient_g=nutrient_g_for_ec(raw.ec_ms_cm, volume_l, params.ec_per_g_per_l),
                ph=raw.ph,
                temp_c=raw.temp_c,
            )
    elif metric == "circFlowLpm":
        zone.pump_flow_lpm = value  # the channel flow while the pump runs
    elif metric.startswith("dayTankPct."):
        line = metric.removeprefix("dayTankPct.")
        zone.day_tank_l[line] = zone.day_tank_usable_l[line] * value / 100.0
    elif water.volume_l <= 0:
        return  # no water to change; the hold applies once there is some
    elif metric == "ec":
        nutrient_g = nutrient_g_for_ec(value, water.volume_l, params.ec_per_g_per_l)
        zone.reservoir = dataclasses.replace(water, nutrient_g=nutrient_g)
    elif metric == "ph":
        zone.reservoir = dataclasses.replace(water, ph=value)
    elif metric == "solTempC":
        zone.reservoir = dataclasses.replace(water, temp_c=value)


def _acted_on(zone: ZoneFarm) -> set[str]:
    """11.4.2: a dose releases ec, ph and the dosed day-tanks; a refill releases the level (and
    ec and ph, which it dilutes). A chiller command will release solTempC once one is handled."""
    released = set()
    for line in zone.day_tank_l:
        if zone.outputs.get(f"{zone.zone_id}-DOSE-{line}") == "ON":
            released |= {"ec", "ph", f"dayTankPct.{line}"}
    if zone.outputs.get(f"{zone.zone_id}-REFILL") == "OPEN":
        released |= {"reservoirPct", "ec", "ph"}
    return released


def _object(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise invalid("body", "The body must be a JSON object")
    return body


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
