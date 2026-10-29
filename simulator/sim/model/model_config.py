"""Build the farm model's parameters from the client config pack.

Reads the "model" block of config/clients/<client>/simulator.json and one zone
entry of layout.json (contract 2.4). Loading the files and validating them
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


def _text(cfg: Mapping[str, Any], key: str, path: str) -> str:
    value = cfg.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path}.{key} must be a non-empty string, got {value!r}")
    return value
