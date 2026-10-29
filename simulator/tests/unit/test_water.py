"""Reservoir model: direction and rough size of every change (T3-1).

Parameters are the explicit fixtures in conftest.py. Expected values are
written as the formula that produces them, so a reader can check each one.
"""

from __future__ import annotations

import dataclasses
import itertools
import math

import pytest

from sim.model.climate import WATER_HEAT_CAPACITY_J_PER_L_K
from sim.model.crops import PlantDemand, plant_demand
from sim.model.params import PlantUptakeParams, ReservoirParams
from sim.model.water import (
    ReservoirState,
    StepInputs,
    dose,
    level_pct,
    readings,
    refill_flow_lps,
    reservoir_from_readings,
    step_reservoir,
)

AIR_24 = StepInputs(air_temp_c=24.0)


def ec(state: ReservoirState, params: ReservoirParams) -> float:
    value = readings(state, params).ec_ms_cm
    assert value is not None
    return value


# --- Dosing raises EC ---------------------------------------------------------


def test_dose_raises_ec_by_the_expected_amount(params, make_reservoir):
    r = make_reservoir(volume_l=600.0, ec=1.0)  # 600 g in 600 L
    after = dose(r, "A", 100.0, params)  # + 100 mL x 0.5 g/mL = 50 g, + 0.1 L

    assert ec(after, params) > ec(r, params)
    assert ec(after, params) == pytest.approx(650.0 / 600.1)
    assert 0.0 < ec(after, params) - ec(r, params) < 0.2  # sensible for 100 mL in 600 L


def test_each_stock_line_adds_its_own_strength(params, make_reservoir):
    r = make_reservoir(volume_l=1000.0, ec=1.0)
    after = dose(dose(r, "A", 100.0, params), "B", 100.0, params)

    assert after.nutrient_g == pytest.approx(1000.0 + 50.0 + 50.0)
    assert after.volume_l == pytest.approx(1000.2)


# --- Refill dilutes ---------------------------------------------------------------


def test_refill_dilutes_ec(params, make_reservoir):
    r = make_reservoir(volume_l=600.0, ec=1.0)
    after = step_reservoir(r, 60.0, StepInputs(air_temp_c=24.0, refill_lps=1.0), params)

    assert after.volume_l == pytest.approx(660.0)
    # 600 g + 60 L of raw water at 0.3 g/L, in 660 L
    assert ec(after, params) == pytest.approx((600.0 + 60.0 * 0.3) / 660.0)
    assert ec(after, params) < ec(r, params)


def test_big_refill_matches_the_prototype_story(params, make_reservoir):
    # Runbook "The solution got too rich": half-full at EC 2.30, refill about
    # 600 L of raw water (EC 0.3, pH 7.2) and EC comes down near 1.3-1.4 while
    # pH climbs toward the raw water's.
    r = make_reservoir(volume_l=600.0, ec=2.30, ph=5.9)
    after = step_reservoir(r, 600.0, StepInputs(air_temp_c=24.0, refill_lps=1.0), params)

    assert ec(after, params) == pytest.approx((600.0 * 2.30 + 600.0 * 0.3) / 1200.0)
    assert after.ph == pytest.approx((600.0 * 5.9 + 600.0 * 7.2) / 1200.0, abs=0.01)
    assert after.ph > r.ph


def test_refill_flow_is_converted_from_litres_per_minute(params):
    assert refill_flow_lps(params) == pytest.approx(60.0 / 60.0)


# --- Overflow ----------------------------------------------------------------------


def test_refill_never_overflows_capacity(params, make_reservoir):
    r = make_reservoir(volume_l=1190.0, ec=1.0)
    after = step_reservoir(r, 600.0, StepInputs(air_temp_c=24.0, refill_lps=1.0), params)

    assert after.volume_l == params.capacity_l
    # 590 L overflowed, carrying nutrients with it: EC is between raw and start.
    assert 0.3 < ec(after, params) < 1.0
    assert after.nutrient_g < r.nutrient_g + 600.0 * 0.3


def test_full_tank_with_matched_flows_stays_full_without_spilling(
    params, make_reservoir
):
    r = make_reservoir(volume_l=1200.0, ec=1.0)
    inputs = StepInputs(air_temp_c=24.0, refill_lps=0.5, plants=PlantDemand(water_lps=0.5))
    after = step_reservoir(r, 3600.0, inputs, params)

    assert after.volume_l == pytest.approx(1200.0)
    # Nothing spilled: every gram of raw-water salt stayed in the tank.
    assert after.nutrient_g == pytest.approx(1200.0 + 1800.0 * 0.3)


def test_dose_into_a_full_tank_stays_at_capacity(params, make_reservoir):
    r = make_reservoir(volume_l=1200.0, ec=1.0)
    after = dose(r, "A", 100.0, params)

    assert after.volume_l == params.capacity_l
    assert ec(after, params) > ec(r, params)


# --- Depletion and zero volume ------------------------------------------------------


def test_plants_drinking_the_tank_dry_stop_at_zero(params, make_reservoir):
    r = make_reservoir(volume_l=10.0, ec=1.0)
    inputs = StepInputs(air_temp_c=24.0, plants=PlantDemand(water_lps=1.0))
    after = step_reservoir(r, 60.0, inputs, params)

    assert after.volume_l == 0.0
    assert after.nutrient_g == pytest.approx(10.0)  # left behind as residue
    assert step_reservoir(after, 60.0, inputs, params) == after  # nothing left to take


def test_refill_slower_than_use_still_ends_at_exactly_zero(params, make_reservoir):
    r = make_reservoir(volume_l=10.0, ec=1.0)
    inputs = StepInputs(air_temp_c=24.0, refill_lps=0.5, plants=PlantDemand(water_lps=1.0))
    after = step_reservoir(r, 60.0, inputs, params)

    # Net -0.5 L/s empties 10 L in 20 s; after that plants take only the inflow.
    assert after.volume_l == 0.0


def test_empty_tank_reports_no_ec_ph_or_temperature(params, make_reservoir):
    r = make_reservoir(volume_l=0.0)
    view = readings(r, params)

    assert view.volume_l == 0.0
    assert view.level_pct == 0.0
    assert view.ec_ms_cm is None
    assert view.ph is None
    assert view.temp_c is None


def test_residue_redissolves_when_water_returns(params, make_reservoir):
    dry = ReservoirState(volume_l=0.0, nutrient_g=10.0, ph=6.0, temp_c=24.0)
    after = step_reservoir(dry, 10.0, StepInputs(air_temp_c=24.0, refill_lps=1.0), params)

    assert after.volume_l == pytest.approx(10.0)
    assert ec(after, params) == pytest.approx((10.0 + 10.0 * 0.3) / 10.0)


def test_refill_into_an_empty_tank_gives_raw_water(params, make_reservoir):
    r = make_reservoir(volume_l=0.0)
    after = step_reservoir(r, 10.0, StepInputs(air_temp_c=26.0, refill_lps=1.0), params)

    assert ec(after, params) == pytest.approx(0.3)
    assert after.ph == pytest.approx(7.2)
    assert after.temp_c == pytest.approx(26.0)


def test_dose_into_an_empty_tank_stays_finite(params, make_reservoir):
    r = make_reservoir(volume_l=0.0)
    after = dose(dose(r, "A", 100.0, params), "ACID", 50.0, params)

    view = readings(after, params)
    assert after.volume_l == pytest.approx(0.15)
    assert view.ec_ms_cm is not None and math.isfinite(view.ec_ms_cm)
    assert view.ph == 0.0  # neat acid: the linear model saturates at the scale's end


def test_zero_capacity_tank_overflows_everything(params):
    no_tank = dataclasses.replace(params, capacity_l=0.0)
    r = reservoir_from_readings(0.0, 0.0, 7.0, 24.0, no_tank)

    after = step_reservoir(r, 60.0, StepInputs(air_temp_c=24.0, refill_lps=1.0), no_tank)
    assert after.volume_l == 0.0
    assert after.nutrient_g == 0.0
    assert dose(r, "A", 10.0, no_tank).volume_l == 0.0
    assert readings(after, no_tank).level_pct is None
    assert level_pct(0.0, 0.0) is None


# --- Nutrient mass stays valid --------------------------------------------------------


def test_leak_loses_water_and_nutrients_together(params, make_reservoir):
    r = make_reservoir(volume_l=1000.0, ec=1.5)
    after = step_reservoir(r, 3600.0, StepInputs(air_temp_c=24.0, leak_lps=0.1), params)

    assert after.volume_l == pytest.approx(640.0)
    assert after.nutrient_g == pytest.approx(1500.0 * 640.0 / 1000.0)
    assert ec(after, params) == pytest.approx(1.5)  # a leak does not change EC


def test_plants_cannot_take_more_nutrient_than_there_is(params, make_reservoir):
    r = make_reservoir(volume_l=100.0, ec=0.1)  # 10 g
    inputs = StepInputs(air_temp_c=24.0, plants=PlantDemand(nutrient_gps=1.0))
    after = step_reservoir(r, 60.0, inputs, params)

    assert after.nutrient_g == 0.0
    assert ec(after, params) == 0.0


@pytest.mark.parametrize("volume_l", [0.0, 0.5, 600.0, 1199.0, 1200.0])
def test_state_stays_physical_for_any_mix_of_flows(params, make_reservoir, volume_l):
    flows = [0.0, 0.01, 1.0, 50.0]
    for refill, leak, drink, uptake in itertools.product(flows, repeat=4):
        inputs = StepInputs(
            air_temp_c=30.0,
            refill_lps=refill,
            leak_lps=leak,
            plants=PlantDemand(water_lps=drink, nutrient_gps=uptake),
        )
        after = step_reservoir(make_reservoir(volume_l, ec=1.5), 600.0, inputs, params)
        assert 0.0 <= after.volume_l <= params.capacity_l
        assert after.nutrient_g >= 0.0
        assert math.isfinite(after.nutrient_g) and math.isfinite(after.temp_c)


# --- Plants use water and slowly lower EC ----------------------------------------------


def test_a_day_of_growing_lettuce(params, plant_params: PlantUptakeParams, make_reservoir):
    r = make_reservoir(volume_l=1000.0, ec=1.5, temp_c=25.0)
    plants = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=25.0)
    after = step_reservoir(r, 24 * 3600.0, StepInputs(air_temp_c=25.0, plants=plants), params)

    # 4 benches x 1.5 L/h x 24 h = 144 L; 4 x 3.0 g/h x 24 h = 288 g
    assert after.volume_l == pytest.approx(1000.0 - 144.0)
    assert ec(after, params) == pytest.approx((1500.0 - 288.0) / 856.0)
    assert -0.2 < ec(after, params) - 1.5 < 0.0  # slowly lower


def test_hot_weather_makes_ec_rise_relative_to_mild(params, plant_params, make_reservoir):
    def ec_after_day(air_c: float) -> float:
        plants = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=air_c)
        r = make_reservoir(volume_l=1000.0, ec=1.5, temp_c=air_c)
        return ec(step_reservoir(r, 86400.0, StepInputs(air_c, plants=plants), params), params)

    assert ec_after_day(35.0) > ec_after_day(25.0)


# --- pH ----------------------------------------------------------------------------------


def test_acid_lowers_ph_by_a_plausible_amount(params, make_reservoir):
    r = make_reservoir(volume_l=936.0, ec=1.51, ph=6.21)
    after = dose(r, "ACID", 30.0, params)

    carrier = (6.21 * 936.0 + 7.2 * 0.03) / 936.03  # the acid's own volume
    assert after.ph == pytest.approx(carrier - 0.006 * 30.0 * 1000.0 / 936.03)
    assert 0.1 < r.ph - after.ph < 0.3


def test_neutral_stock_barely_moves_ph(params, make_reservoir):
    r = make_reservoir(volume_l=936.0, ph=6.21)
    after = dose(r, "A", 120.0, params)

    assert abs(after.ph - r.ph) < 0.001


def test_acid_effect_is_stronger_in_a_smaller_volume(params, make_reservoir):
    def drop(volume_l: float) -> float:
        r = make_reservoir(volume_l=volume_l, ph=6.5)
        return r.ph - dose(r, "ACID", 30.0, params).ph

    assert drop(300.0) > drop(1200.0)


def test_ph_drifts_up_slowly_toward_its_target(params, make_reservoir):
    r = make_reservoir(volume_l=1000.0, ph=5.8, temp_c=24.0)
    after = step_reservoir(r, 24 * 3600.0, AIR_24, params)

    assert after.ph == pytest.approx(7.2 - (7.2 - 5.8) * math.exp(-24.0 / 168.0))
    assert 5.8 < after.ph < 7.2


def test_extreme_acid_dose_stays_on_the_ph_scale(params, make_reservoir):
    r = make_reservoir(volume_l=1.0, ph=6.0)
    assert dose(r, "ACID", 1000.0, params).ph == 0.0


# --- Solution temperature ------------------------------------------------------------------


def test_solution_warms_gradually_toward_the_air(params, make_reservoir):
    r = make_reservoir(volume_l=1000.0, temp_c=20.0)
    hot_air = StepInputs(air_temp_c=30.0)
    tau_s = 1000.0 * WATER_HEAT_CAPACITY_J_PER_L_K / params.air_coupling_w_per_k

    one_minute = step_reservoir(r, 60.0, hot_air, params)
    one_hour = step_reservoir(r, 3600.0, hot_air, params)

    assert 20.0 < one_minute.temp_c < 20.05
    assert one_hour.temp_c == pytest.approx(30.0 - 10.0 * math.exp(-3600.0 / tau_s))


def test_temperature_never_overshoots_the_air_on_a_long_step(params, make_reservoir):
    r = make_reservoir(volume_l=1000.0, temp_c=20.0)
    after = step_reservoir(r, 1e7, StepInputs(air_temp_c=30.0), params)

    assert after.temp_c == pytest.approx(30.0)
    assert after.temp_c <= 30.0


def test_smaller_volume_follows_the_air_faster(params, make_reservoir):
    def warmed(volume_l: float) -> float:
        r = make_reservoir(volume_l=volume_l, temp_c=20.0)
        return step_reservoir(r, 3600.0, StepInputs(air_temp_c=30.0), params).temp_c

    assert warmed(500.0) > warmed(1000.0)


# --- Invalid input -------------------------------------------------------------------------


@pytest.mark.parametrize("dt_s", [-1.0, math.nan, math.inf])
def test_rejects_bad_time_steps(params, make_reservoir, dt_s):
    with pytest.raises(ValueError, match="dt_s"):
        step_reservoir(make_reservoir(600.0), dt_s, AIR_24, params)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"refill_lps": -0.1},
        {"leak_lps": -0.1},
        {"refill_lps": math.nan},
        {"air_temp_c": math.inf},
    ],
)
def test_rejects_bad_step_inputs(kwargs):
    with pytest.raises(ValueError):
        StepInputs(**{"air_temp_c": 24.0, **kwargs})


def test_rejects_negative_plant_demand():
    with pytest.raises(ValueError):
        PlantDemand(water_lps=-1.0)


@pytest.mark.parametrize("ml", [-1.0, math.nan, True])
def test_rejects_bad_dose_amounts(params, make_reservoir, ml):
    with pytest.raises(ValueError, match="ml"):
        dose(make_reservoir(600.0), "A", ml, params)


def test_rejects_an_unconfigured_dosing_line(params, make_reservoir):
    with pytest.raises(ValueError, match="BASE"):
        dose(make_reservoir(600.0), "BASE", 10.0, params)


@pytest.mark.parametrize(
    "fields",
    [
        {"volume_l": -1.0},
        {"nutrient_g": -0.1},
        {"ph": 14.5},
        {"temp_c": math.nan},
    ],
)
def test_rejects_unphysical_states(fields):
    with pytest.raises(ValueError):
        ReservoirState(**{"volume_l": 1.0, "nutrient_g": 1.0, "ph": 6.0, "temp_c": 24.0, **fields})


def test_rejects_a_state_larger_than_the_tank(params):
    too_full = ReservoirState(volume_l=1300.0, nutrient_g=0.0, ph=6.0, temp_c=24.0)
    with pytest.raises(ValueError, match="capacity"):
        step_reservoir(too_full, 1.0, AIR_24, params)
    with pytest.raises(ValueError, match="capacity"):
        reservoir_from_readings(1300.0, 1.0, 6.0, 24.0, params)


@pytest.mark.parametrize(
    "fields",
    [
        {"capacity_l": -1.0},
        {"ec_per_g_per_l": 0.0},
        {"ph_drift_time_constant_h": 0.0},
        {"max_substep_s": 0.0},
        {"max_substeps": 0},
        {"max_substeps": 2.5},
    ],
)
def test_rejects_invalid_parameters(params, fields):
    with pytest.raises(ValueError):
        dataclasses.replace(params, **fields)


# --- Determinism and long steps ----------------------------------------------------------------


def _busy_inputs() -> StepInputs:
    """Refill, leak and plants at once, without reaching the overflow."""
    return StepInputs(
        air_temp_c=31.0,
        refill_lps=0.1,
        leak_lps=0.05,
        plants=PlantDemand(water_lps=0.002, nutrient_gps=0.004),
    )


def test_same_inputs_give_identical_results(params, make_reservoir):
    def run() -> ReservoirState:
        r = make_reservoir(volume_l=800.0, ec=1.6, ph=6.1, temp_c=22.0)
        for _ in range(100):
            r = dose(step_reservoir(r, 30.0, _busy_inputs(), params), "A", 1.0, params)
        return r

    assert run() == run()


def test_one_long_step_matches_many_short_steps(params, make_reservoir):
    start = make_reservoir(volume_l=800.0, ec=1.6, ph=6.1, temp_c=22.0)
    one_call = step_reservoir(start, 3600.0, _busy_inputs(), params)

    by_minute = start
    for _ in range(60):
        by_minute = step_reservoir(by_minute, 60.0, _busy_inputs(), params)
    by_second = start
    for _ in range(3600):
        by_second = step_reservoir(by_second, 1.0, _busy_inputs(), params)

    assert one_call == by_minute  # same internal steps, bit for bit
    for field in ("volume_l", "nutrient_g", "ph", "temp_c"):
        assert getattr(one_call, field) == pytest.approx(getattr(by_second, field), rel=1e-3)


def test_very_long_step_is_capped_but_stays_valid(params, make_reservoir):
    coarse = dataclasses.replace(params, max_substeps=1)
    start = make_reservoir(volume_l=800.0, ec=1.6)
    after = step_reservoir(start, 30 * 86400.0, _busy_inputs(), coarse)

    assert 0.0 <= after.volume_l <= coarse.capacity_l
    assert after.nutrient_g >= 0.0
    assert step_reservoir(start, 0.0, _busy_inputs(), params) is start
