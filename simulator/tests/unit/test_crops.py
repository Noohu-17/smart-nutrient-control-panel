"""Plant uptake rates (T3-1)."""

from __future__ import annotations

import pytest

from sim.model.crops import NO_PLANTS, plant_demand


def test_uptake_scales_with_benches_and_converts_to_per_second(plant_params):
    demand = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=25.0)

    assert demand.water_lps == pytest.approx(4 * 1.5 / 3600.0)
    assert demand.nutrient_gps == pytest.approx(4 * 3.0 / 3600.0)


def test_later_stage_takes_more_than_seedlings(plant_params):
    seedling = plant_demand(plant_params, "LETTUCE", "SEEDLING", 4, air_temp_c=25.0)
    growing = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=25.0)

    assert growing.water_lps > seedling.water_lps
    assert growing.nutrient_gps > seedling.nutrient_gps


def test_heat_raises_water_uptake_only(plant_params):
    mild = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=25.0)
    hot = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=35.0)

    assert hot.water_lps == pytest.approx(mild.water_lps * (1.0 + 0.03 * 10.0))
    assert hot.nutrient_gps == mild.nutrient_gps


def test_water_uptake_never_goes_negative_in_extreme_cold(plant_params):
    frozen = plant_demand(plant_params, "LETTUCE", "GROWING", 4, air_temp_c=-100.0)
    assert frozen.water_lps == 0.0


def test_no_benches_planted_means_no_uptake(plant_params):
    assert plant_demand(plant_params, "LETTUCE", "GROWING", 0, air_temp_c=25.0) == NO_PLANTS


@pytest.mark.parametrize(
    ("crop", "stage", "benches"),
    [("BASIL", "GROWING", 1), ("LETTUCE", "READY", 1), ("LETTUCE", "GROWING", -1)],
)
def test_rejects_unknown_crops_stages_and_negative_benches(plant_params, crop, stage, benches):
    with pytest.raises(ValueError):
        plant_demand(plant_params, crop, stage, benches, air_temp_c=25.0)


@pytest.mark.parametrize("benches", [1.5, True])
def test_rejects_non_integer_bench_counts(plant_params, benches):
    with pytest.raises(ValueError, match="benches_planted"):
        plant_demand(plant_params, "LETTUCE", "GROWING", benches, air_temp_c=25.0)
