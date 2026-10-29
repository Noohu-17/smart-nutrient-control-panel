"""Explicit test parameters for the farm model unit tests.

These numbers are chosen for easy hand-checked arithmetic, not taken from
simulator.json, so the maths tests don't move when the config changes. The
config itself is tested in test_model_config.py.

    capacity 1200 L; k = 1.0 mS/cm per g/L (so EC equals g/L)
    raw water EC 0.3, pH 7.2, 26 C
    stock A and B: 0.5 g/mL, pH-neutral; acid: -0.006 pH per mL per 1000 L
    pH drifts toward 7.2 with a 168 h time constant
    air coupling 232.5 W/K: tau = 1000 L * 4186 / 232.5 = 18004 s (about 5 h)
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from sim.model.params import (
    DoseLineEffect,
    PlantUptakeParams,
    RawWater,
    ReservoirParams,
    StageUptake,
)
from sim.model.water import ReservoirState, reservoir_from_readings


@pytest.fixture
def params() -> ReservoirParams:
    return ReservoirParams(
        capacity_l=1200.0,
        ec_per_g_per_l=1.0,
        raw_water=RawWater(ec_ms_cm=0.3, ph=7.2, temp_c=26.0),
        dose_lines={
            "A": DoseLineEffect(nutrient_g_per_ml=0.5, ph_change_per_ml_per_1000l=0.0),
            "B": DoseLineEffect(nutrient_g_per_ml=0.5, ph_change_per_ml_per_1000l=0.0),
            "ACID": DoseLineEffect(nutrient_g_per_ml=0.0, ph_change_per_ml_per_1000l=-0.006),
        },
        refill_flow_lpm=60.0,
        ph_drift_target=7.2,
        ph_drift_time_constant_h=168.0,
        air_coupling_w_per_k=232.5,
        max_substep_s=60.0,
        max_substeps=100_000,
    )


@pytest.fixture
def plant_params() -> PlantUptakeParams:
    growing = StageUptake(water_l_per_bench_h=1.5, nutrient_g_per_bench_h=3.0)
    return PlantUptakeParams(
        reference_air_temp_c=25.0,
        water_change_per_c=0.03,
        crops={
            "LETTUCE": {
                "SEEDLING": StageUptake(water_l_per_bench_h=0.4, nutrient_g_per_bench_h=0.8),
                "GROWING": growing,
            }
        },
    )


@pytest.fixture
def make_reservoir(params: ReservoirParams) -> Callable[..., ReservoirState]:
    def make(
        volume_l: float, ec: float = 1.0, ph: float = 6.0, temp_c: float = 24.0
    ) -> ReservoirState:
        return reservoir_from_readings(volume_l, ec, ph, temp_c, params)

    return make
