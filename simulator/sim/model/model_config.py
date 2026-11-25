"""Build the farm model's parameters from the client config pack.

Reads the "model" block of config/clients/<client>/simulator.json and one zone
entry of layout.json (contract 2.4), plus the values the farm has no physics for
yet (air, power, day-tank levels): those are held constant until a weather model
and the power drills exist. Loading the files and validating them
against the JSON Schemas is done elsewhere (Track 4's sim/config.py and
Track 1's scripts/validate_config.py); this module only turns already-loaded
JSON into typed, checked parameters, and reports the exact key at fault.

Keys are lowerCamelCase with the unit in the name (contract section 1).
Unknown keys are ignored (contract section 1). Where each value came from is
recorded in the file's _meta.sources. Dosing lines come from the zone's
doseLines, never a fixed A/B/ACID list (OPEN-12): every installed line needs an
entry, and entries for lines a zone doesn't have are ignored.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .params import (
    DoseLineEffect,
    PlantUptakeParams,
    RawWater,
    ReservoirParams,
    StageUptake,
    require_finite,
    require_non_negative,
    require_ph,
)
from .water import ReservoirState, reservoir_from_readings

PERCENT_MAX = 100.0
PHASES = ("l1V", "l2V", "l3V")  # the three phases contract 6.3 reports


@dataclass(frozen=True, slots=True)
class InitialState:
    """Default starting water; a scenario (scenarios.json) may override it."""

    reservoir_pct: float
    ec_ms_cm: float
    ph: float
    sol_temp_c: float

    def __post_init__(self) -> None:
        require_non_negative("reservoir_pct", self.reservoir_pct)
        if self.reservoir_pct > 100.0:
            raise ValueError(f"reservoir_pct must be <= 100, got {self.reservoir_pct!r}")
        require_non_negative("ec_ms_cm", self.ec_ms_cm)
        require_ph("ph", self.ph)
        require_finite("sol_temp_c", self.sol_temp_c)


@dataclass(frozen=True, slots=True)
class ZoneModelParams:
    zone_id: str
    reservoir: ReservoirParams
    plants: PlantUptakeParams
    initial: InitialState


def parse_zone_model(model: Mapping[str, Any], zone: Mapping[str, Any]) -> ZoneModelParams:
    """Parameters for one zone from simulator.json["model"] and a layout zone."""
    zone_id = _text(zone, "id", "layout zone")
    where = f"layout zone {zone_id}"
    reservoir_cfg = _section(zone, "reservoir", where)
    line_ids = zone.get("doseLines")
    if not isinstance(line_ids, list) or not all(isinstance(x, str) for x in line_ids):
        raise ValueError(f"{where}.doseLines must be a list of line IDs")

    raw = _section(model, "rawWater", "model")
    lines_cfg = _section(model, "doseLines", "model")
    dose_lines = {}
    for line in line_ids:
        cfg = _section(lines_cfg, line, "model.doseLines")
        path = f"model.doseLines.{line}"
        dose_lines[line] = _build(
            path,
            DoseLineEffect,
            nutrient_g_per_ml=_number(cfg, "nutrientGPerMl", path),
            ph_change_per_ml_per_1000l=_number(cfg, "phChangePerMlPer1000L", path),
        )

    refill = _section(model, "refill", "model")
    drift = _section(model, "phDrift", "model")
    temp = _section(model, "solutionTemp", "model")
    numerics = _section(model, "numerics", "model")
    reservoir = _build(
        "model",
        ReservoirParams,
        capacity_l=_number(reservoir_cfg, "usableL", f"{where}.reservoir"),
        ec_per_g_per_l=_number(model, "ecPerGramPerL", "model"),
        raw_water=_build(
            "model.rawWater",
            RawWater,
            ec_ms_cm=_number(raw, "ecMsCm", "model.rawWater"),
            ph=_number(raw, "ph", "model.rawWater"),
            temp_c=_number(raw, "tempC", "model.rawWater"),
        ),
        dose_lines=dose_lines,
        refill_flow_lpm=_number(refill, "flowLpm", "model.refill"),
        ph_drift_target=_number(drift, "targetPh", "model.phDrift"),
        ph_drift_time_constant_h=_number(drift, "timeConstantH", "model.phDrift"),
        air_coupling_w_per_k=_number(temp, "airCouplingWPerK", "model.solutionTemp"),
        max_substep_s=_number(numerics, "maxSubstepS", "model.numerics"),
        max_substeps=_integer(numerics, "maxSubstepsPerCall", "model.numerics"),
    )

    return ZoneModelParams(
        zone_id=zone_id,
        reservoir=reservoir,
        plants=_parse_plants(_section(model, "plantUptake", "model")),
        initial=_parse_initial(_section(model, "initialState", "model")),
    )


@dataclass(frozen=True, slots=True)
class DayTank:
    """One dosing line's concentrate tank (layout dayTanks, model.initialState)."""

    usable_l: float
    start_l: float


@dataclass(frozen=True, slots=True)
class AirConditions:
    """The air inside and around the polyhouse, in contract units (6.2)."""

    temp_by_pos_c: Mapping[str, float]
    rh: float
    outside_temp_c: float
    outside_rh: float
    elec_room_temp_c: float

    @property
    def temp_c(self) -> float:
        """Inside air: the average of the sensor positions (contract 6.2)."""
        return sum(self.temp_by_pos_c.values()) / len(self.temp_by_pos_c)


@dataclass(frozen=True, slots=True)
class AirCycle:
    """A simple day in the polyhouse (simulator.json model.air.dailyCycle): the air is warmest, and
    driest, at warmestHour (local time) and coolest twelve hours later. The swings are the full
    range from coolest to warmest. Without it the air is held at model.air (a playground holds
    it)."""

    temp_swing_c: float
    rh_swing_pct: float
    warmest_hour: float

    def air_at(self, base: AirConditions, local_hour: float) -> AirConditions:
        """The air at this local time of day, around `base` (model.air: the day's average)."""
        swing = math.cos(2 * math.pi * (local_hour - self.warmest_hour) / 24.0)
        warmer = self.temp_swing_c / 2 * swing
        drier = self.rh_swing_pct / 2 * swing

        def rh(value: float) -> float:
            return min(PERCENT_MAX, max(0.0, value - drier))

        return AirConditions(
            temp_by_pos_c={pos: t + warmer for pos, t in base.temp_by_pos_c.items()},
            rh=rh(base.rh),
            outside_temp_c=base.outside_temp_c + warmer,
            outside_rh=rh(base.outside_rh),
            elec_room_temp_c=base.elec_room_temp_c,
        )


@dataclass(frozen=True, slots=True)
class PowerSupply:
    """What the power monitor measures while mains is healthy (contract 6.3)."""

    phases_v: Mapping[str, float]
    ups_battery_pct: float
    ups_runtime_min: float


def parse_day_tanks(model: Mapping[str, Any], zone: Mapping[str, Any]) -> dict[str, DayTank]:
    """Day-tanks by dosing line, for the lines this zone has (no fixed A/B/ACID, OPEN-12)."""
    zone_id = _text(zone, "id", "layout zone")
    tanks_cfg = _section(zone, "dayTanks", f"layout zone {zone_id}")
    levels = _section(_section(model, "initialState", "model"), "dayTankPct", "model.initialState")
    tanks = {}
    for line in zone.get("doseLines", ()):
        usable_l = _number(
            _section(tanks_cfg, line, f"layout zone {zone_id}.dayTanks"),
            "usableL",
            f"layout zone {zone_id}.dayTanks.{line}",
        )
        pct = _percent(levels, line, "model.initialState.dayTankPct")
        tanks[line] = DayTank(usable_l=usable_l, start_l=usable_l * pct / PERCENT_MAX)
    return tanks


def parse_circulation_flow_lpm(model: Mapping[str, Any]) -> float:
    """Channel flow while the circulation pump runs (contract 6.1 circFlowLpm)."""
    flow_lpm = _number(_section(model, "circulation", "model"), "flowLpm", "model.circulation")
    if flow_lpm < 0.0:
        raise ValueError(f"model.circulation.flowLpm must be >= 0, got {flow_lpm}")
    return flow_lpm


def parse_air(model: Mapping[str, Any], air_sensors: Any) -> AirConditions:
    """The air, with one temperature per layout.climate.airSensors position."""
    path = "model.air"
    if not isinstance(air_sensors, list) or not air_sensors:
        raise ValueError(f"layout.climate.airSensors must list positions, got {air_sensors!r}")
    cfg = _section(model, "air", "model")
    temps = _section(cfg, "tempByPosC", path)
    return AirConditions(
        temp_by_pos_c={pos: _number(temps, pos, f"{path}.tempByPosC") for pos in air_sensors},
        rh=_percent(cfg, "rh", path),
        outside_temp_c=_number(cfg, "outsideTempC", path),
        outside_rh=_percent(cfg, "outsideRh", path),
        elec_room_temp_c=_number(cfg, "elecRoomTempC", path),
    )


def parse_air_cycle(model: Mapping[str, Any]) -> AirCycle | None:
    """model.air.dailyCycle, or None when the pack has none (the air stays as model.air)."""
    cfg = _section(model, "air", "model").get("dailyCycle")
    if cfg is None:
        return None
    path = "model.air.dailyCycle"
    if not isinstance(cfg, Mapping):
        raise ValueError(f"{path} must be an object, got {cfg!r}")
    temp_swing_c = _number(cfg, "tempSwingC", path)
    rh_swing_pct = _number(cfg, "rhSwingPct", path)
    warmest_hour = _number(cfg, "warmestHour", path)
    if temp_swing_c < 0 or rh_swing_pct < 0:
        raise ValueError(f"{path}: tempSwingC and rhSwingPct must be >= 0")
    if not 0 <= warmest_hour < 24:
        raise ValueError(f"{path}.warmestHour must be from 0 to under 24, got {warmest_hour}")
    return AirCycle(temp_swing_c, rh_swing_pct, warmest_hour)


def parse_power(model: Mapping[str, Any]) -> PowerSupply:
    path = "model.power"
    cfg = _section(model, "power", "model")
    phases = _section(cfg, "phasesV", path)
    return PowerSupply(
        phases_v={phase: _number(phases, phase, f"{path}.phasesV") for phase in PHASES},
        ups_battery_pct=_percent(cfg, "upsBatteryPct", path),
        ups_runtime_min=_number(cfg, "upsRuntimeMin", path),
    )


def parse_chiller_setpoint(model: Mapping[str, Any], allowed: Mapping[str, Any]) -> float:
    """The chillers' starting setpoint, inside climate.json chillerSetpointC (contract 2.3)."""
    path = "model.initialState"
    setpoint_c = _number(_section(model, "initialState", "model"), "chillerSetpointC", path)
    low = _number(allowed, "min", "climate.json chillerSetpointC")
    high = _number(allowed, "max", "climate.json chillerSetpointC")
    if not low <= setpoint_c <= high:
        raise ValueError(f"{path}.chillerSetpointC must be within {low}-{high}, got {setpoint_c}")
    return setpoint_c


def initial_reservoir(zone: ZoneModelParams) -> ReservoirState:
    """The zone's reservoir in its configured default starting state."""
    start = zone.initial
    return reservoir_from_readings(
        volume_l=zone.reservoir.capacity_l * start.reservoir_pct / 100.0,
        ec=start.ec_ms_cm,
        ph=start.ph,
        temp_c=start.sol_temp_c,
        params=zone.reservoir,
    )


def _parse_plants(cfg: Mapping[str, Any]) -> PlantUptakeParams:
    path = "model.plantUptake"
    crops_cfg = _section(cfg, "crops", path)
    crops: dict[str, dict[str, StageUptake]] = {}
    for crop, stages_cfg in crops_cfg.items():
        stages = _section(crops_cfg, crop, f"{path}.crops")
        crops[crop] = {}
        for stage in stages_cfg:
            stage_path = f"{path}.crops.{crop}.{stage}"
            stage_cfg = _section(stages, stage, f"{path}.crops.{crop}")
            crops[crop][stage] = _build(
                stage_path,
                StageUptake,
                water_l_per_bench_h=_number(stage_cfg, "waterLPerBenchPerH", stage_path),
                nutrient_g_per_bench_h=_number(stage_cfg, "nutrientGPerBenchPerH", stage_path),
            )
    return _build(
        path,
        PlantUptakeParams,
        reference_air_temp_c=_number(cfg, "referenceAirTempC", path),
        water_change_per_c=_number(cfg, "waterChangePerDegC", path),
        crops=crops,
    )


def _parse_initial(cfg: Mapping[str, Any]) -> InitialState:
    path = "model.initialState"
    return _build(
        path,
        InitialState,
        reservoir_pct=_number(cfg, "reservoirPct", path),
        ec_ms_cm=_number(cfg, "ecMsCm", path),
        ph=_number(cfg, "ph", path),
        sol_temp_c=_number(cfg, "solTempC", path),
    )


def _build(path: str, cls: type, **fields: Any) -> Any:
    """Construct a params object, prefixing any validation error with its config path."""
    try:
        return cls(**fields)
    except ValueError as err:
        raise ValueError(f"{path}: {err}") from None


def _section(cfg: Mapping[str, Any], key: str, path: str) -> Mapping[str, Any]:
    value = cfg.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{path}.{key} must be an object, got {value!r}")
    return value


def _number(cfg: Mapping[str, Any], key: str, path: str) -> float:
    value = cfg.get(key)
    try:
        require_finite(f"{path}.{key}", value)
    except ValueError:
        raise ValueError(f"{path}.{key} must be a finite number, got {value!r}") from None
    return float(value)


def _integer(cfg: Mapping[str, Any], key: str, path: str) -> int:
    value = cfg.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path}.{key} must be an integer, got {value!r}")
    return value


def _percent(cfg: Mapping[str, Any], key: str, path: str) -> float:
    value = _number(cfg, key, path)
    if not 0.0 <= value <= PERCENT_MAX:
        raise ValueError(f"{path}.{key} must be within 0-100, got {value}")
    return value


def _text(cfg: Mapping[str, Any], key: str, path: str) -> str:
    value = cfg.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}.{key} must be a non-empty string, got {value!r}")
    return value
