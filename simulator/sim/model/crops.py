"""Plant uptake: how fast the crop in a zone takes water and nutrients.

Water and nutrients are taken separately, so the configured rates decide
whether EC falls (plants take nutrients faster than water) or rises (hot
weather: plants drink more, nutrient uptake stays the same).

Simplifications: uptake is per planted bench at the stage's rate, with no
day/night cycle and no slowdown at low nutrient concentration; the reservoir
model stops uptake when the water or the dissolved nutrients run out.
"""

from __future__ import annotations

from dataclasses import dataclass

from .params import PlantUptakeParams, require_finite, require_non_negative

_S_PER_H = 3600.0


@dataclass(frozen=True, slots=True)
class PlantDemand:
    """What the plants would take per second if the reservoir can supply it."""

    water_lps: float = 0.0
    nutrient_gps: float = 0.0

    def __post_init__(self) -> None:
        require_non_negative("water_lps", self.water_lps)
        require_non_negative("nutrient_gps", self.nutrient_gps)


NO_PLANTS = PlantDemand()


def plant_demand(
    params: PlantUptakeParams,
    crop: str,
    stage: str,
    benches_planted: int,
    air_temp_c: float,
) -> PlantDemand:
    """Uptake rates for benches_planted benches of crop at stage.

    Water uptake scales by (1 + water_change_per_c * (air - reference)), never
    below zero. benches_planted is checked against the layout by the scenario
    loader (Track 4), not here.
    """
    if isinstance(benches_planted, bool) or not isinstance(benches_planted, int):
        raise ValueError(f"benches_planted must be an int, got {benches_planted!r}")
    if benches_planted < 0:
        raise ValueError(f"benches_planted must be >= 0, got {benches_planted}")
    require_finite("air_temp_c", air_temp_c)
    try:
        uptake = params.crops[crop][stage]
    except KeyError:
        raise ValueError(f"no uptake configured for crop {crop!r} at stage {stage!r}") from None
    if benches_planted == 0:
        return NO_PLANTS

    heat = params.water_change_per_c * (air_temp_c - params.reference_air_temp_c)
    water_factor = max(0.0, 1.0 + heat)
    return PlantDemand(
        water_lps=uptake.water_l_per_bench_h * benches_planted * water_factor / _S_PER_H,
        nutrient_gps=uptake.nutrient_g_per_bench_h * benches_planted / _S_PER_H,
    )
