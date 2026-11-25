"""Typed parameters for the farm model.

Every number the model uses arrives through these dataclasses; none is typed
into the model code (simulator guide section 5: "every rate and constant comes
from simulator.json or hardware.json, never from code"). Units are part of each
field name. model_config.py builds these objects from the client config pack.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# The pH scale of aqueous solutions: a definition, not a farm value.
PH_SCALE_MIN = 0.0
PH_SCALE_MAX = 14.0


def require_finite(name: str, value: float) -> None:
    """Raise ValueError unless value is a real, finite number (a bool is not)."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a finite number, got {value!r}")


def require_non_negative(name: str, value: float) -> None:
    require_finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")


def require_positive(name: str, value: float) -> None:
    require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")


def require_ph(name: str, value: float) -> None:
    require_finite(name, value)
    if not PH_SCALE_MIN <= value <= PH_SCALE_MAX:
        raise ValueError(
            f"{name} must be within {PH_SCALE_MIN}-{PH_SCALE_MAX}, got {value!r}"
        )


@dataclass(frozen=True, slots=True)
class RawWater:
    """The water that refills the reservoir and that the stocks are made up with."""

    ec_ms_cm: float
    ph: float
    temp_c: float

    def __post_init__(self) -> None:
        require_non_negative("raw_water.ec_ms_cm", self.ec_ms_cm)
        require_ph("raw_water.ph", self.ph)
        require_finite("raw_water.temp_c", self.temp_c)


@dataclass(frozen=True, slots=True)
class DoseLineEffect:
    """What one millilitre of a dosing line's stock does to the reservoir.

    nutrient_g_per_ml: dissolved salt mass it adds (raises EC through k).
    ph_change_per_ml_per_1000l: pH shift when 1 mL is mixed into 1000 L.
        Negative for acid, positive for a base line (OPEN-4), 0 if neutral.
    """

    nutrient_g_per_ml: float
    ph_change_per_ml_per_1000l: float

    def __post_init__(self) -> None:
        require_non_negative("nutrient_g_per_ml", self.nutrient_g_per_ml)
        require_finite("ph_change_per_ml_per_1000l", self.ph_change_per_ml_per_1000l)


@dataclass(frozen=True, slots=True)
class ReservoirParams:
    """Everything one zone's reservoir model needs.

    capacity_l: usable volume (layout.json zones[].reservoir.usableL).
    ec_per_g_per_l: k in EC = k * M / V, in mS/cm per g/L.
    dose_lines: keyed by the zone's doseLines IDs (no fixed A/B/ACID, OPEN-12).
    refill_flow_lpm: flow through the refill valve while it is open.
    ph_drift_target / ph_drift_time_constant_h: pH relaxes toward the target;
        after one time constant 63 % of the gap has closed.
    air_coupling_w_per_k: heat exchanged with the air per kelvin of difference.
    max_substep_s / max_substeps: numerical settings, not farm values. Long
        steps are split into internal steps no longer than max_substep_s, but
        never more than max_substeps of them per call.
    """

    capacity_l: float
    ec_per_g_per_l: float
    raw_water: RawWater
    dose_lines: Mapping[str, DoseLineEffect]
    refill_flow_lpm: float
    ph_drift_target: float
    ph_drift_time_constant_h: float
    air_coupling_w_per_k: float
    max_substep_s: float
    max_substeps: int

    def __post_init__(self) -> None:
        require_non_negative("capacity_l", self.capacity_l)
        require_positive("ec_per_g_per_l", self.ec_per_g_per_l)
        if not isinstance(self.raw_water, RawWater):
            raise ValueError("raw_water must be a RawWater")
        for line, effect in self.dose_lines.items():
            if not isinstance(effect, DoseLineEffect):
                raise ValueError(f"dose_lines[{line!r}] must be a DoseLineEffect")
        require_non_negative("refill_flow_lpm", self.refill_flow_lpm)
        require_ph("ph_drift_target", self.ph_drift_target)
        require_positive("ph_drift_time_constant_h", self.ph_drift_time_constant_h)
        require_non_negative("air_coupling_w_per_k", self.air_coupling_w_per_k)
        require_positive("max_substep_s", self.max_substep_s)
        if (
            isinstance(self.max_substeps, bool)
            or not isinstance(self.max_substeps, int)
            or self.max_substeps < 1
        ):
            raise ValueError(f"max_substeps must be an int >= 1, got {self.max_substeps!r}")
        # Freeze the mapping so a params object shared by several callers can't change.
        object.__setattr__(self, "dose_lines", MappingProxyType(dict(self.dose_lines)))


@dataclass(frozen=True, slots=True)
class StageUptake:
    """Water and nutrient uptake of one planted bench at one growth stage."""

    water_l_per_bench_h: float
    nutrient_g_per_bench_h: float

    def __post_init__(self) -> None:
        require_non_negative("water_l_per_bench_h", self.water_l_per_bench_h)
        require_non_negative("nutrient_g_per_bench_h", self.nutrient_g_per_bench_h)


@dataclass(frozen=True, slots=True)
class PlantUptakeParams:
    """Plant uptake per crop and stage, and how water uptake follows the heat.

    crops: {crop: {stage: StageUptake}}, using the crop and stage IDs of the
        scenario (contract 11.4, for example LETTUCE / GROWING).
    water_change_per_c: fractional change in water uptake per degree C of air
        temperature above reference_air_temp_c (0.03 means +3 % per degree).
        Nutrient uptake does not follow the heat, so hot weather raises EC.
    """

    reference_air_temp_c: float
    water_change_per_c: float
    crops: Mapping[str, Mapping[str, StageUptake]]

    def __post_init__(self) -> None:
        require_finite("reference_air_temp_c", self.reference_air_temp_c)
        require_non_negative("water_change_per_c", self.water_change_per_c)
        frozen: dict[str, Mapping[str, StageUptake]] = {}
        for crop, stages in self.crops.items():
            for stage, uptake in stages.items():
                if not isinstance(uptake, StageUptake):
                    raise ValueError(f"crops[{crop!r}][{stage!r}] must be a StageUptake")
            frozen[crop] = MappingProxyType(dict(stages))
        object.__setattr__(self, "crops", MappingProxyType(frozen))
