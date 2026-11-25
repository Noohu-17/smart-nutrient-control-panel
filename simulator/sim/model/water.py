"""One zone's reservoir: volume, dissolved nutrients, pH and temperature.

The state is immutable and every function returns a new state, so the model is
pure and deterministic. On each tick Track 4's clock calls step_reservoir()
with the simulated dt; a dosing pump (hardware/) calls dose() with the
millilitres it delivered in that tick.

Water balance (exact for constant flows, whatever the step length):
    refill water mixes in first; plants and the leak then take what they want,
    limited to what is there; anything above capacity overflows.
Dissolved nutrients leave with leak and overflow water in proportion to the
water lost. Plants take nutrients separately from water (crops.py), so the
configured crop decides whether EC drifts down or up.

Long steps are split into internal steps of at most max_substep_s so that
mixing during simultaneous inflow and outflow stays accurate; every internal
step is unconditionally stable, so even a capped, very long step stays valid.

Edge cases:
    Zero volume: dissolved mass stays behind as residue (mass is conserved) and
        redissolves when water returns; readings() reports no EC, pH or
        solution temperature, and plants can take nothing.
    Zero capacity: everything that arrives overflows; level is undefined (None).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .climate import solution_temp_after
from .crops import NO_PLANTS, PlantDemand
from .mixing import mix, relax_toward
from .nutrients import ML_PER_L, clamp_ph, ec_ms_cm, nutrient_g_for_ec, ph_shift
from .params import (
    ReservoirParams,
    require_finite,
    require_non_negative,
    require_ph,
)

_S_PER_MIN = 60.0
_S_PER_H = 3600.0


@dataclass(frozen=True, slots=True)
class ReservoirState:
    """The true state of the solution, before any sensor effects.

    ph and temp_c have no meaning while volume_l is 0; read them through
    readings(), which reports None for an empty tank.
    """

    volume_l: float
    nutrient_g: float
    ph: float
    temp_c: float

    def __post_init__(self) -> None:
        require_non_negative("volume_l", self.volume_l)
        require_non_negative("nutrient_g", self.nutrient_g)
        require_ph("ph", self.ph)
        require_finite("temp_c", self.temp_c)


@dataclass(frozen=True, slots=True)
class StepInputs:
    """What acts on the reservoir during one step, held constant over the step.

    refill_lps: raw water in through the refill valve (0 when closed or when
        the supply has failed).
    leak_lps: solution lost through a leak (a fault Track 4 switches on).
    plants: crop uptake from crops.plant_demand().
    """

    air_temp_c: float
    refill_lps: float = 0.0
    leak_lps: float = 0.0
    plants: PlantDemand = NO_PLANTS

    def __post_init__(self) -> None:
        require_finite("air_temp_c", self.air_temp_c)
        require_non_negative("refill_lps", self.refill_lps)
        require_non_negative("leak_lps", self.leak_lps)
        if not isinstance(self.plants, PlantDemand):
            raise ValueError("plants must be a PlantDemand")


@dataclass(frozen=True, slots=True)
class ReservoirReadings:
    """True values in contract units, unrounded. Contract field in brackets.

    volume_l [reservoirL], level_pct [reservoirPct], ec_ms_cm [ec], ph [ph],
    temp_c [solTempC]. None means there is nothing to measure; turning that
    into null plus a quality flag is the sensor layer's job.
    """

    volume_l: float
    level_pct: float | None
    ec_ms_cm: float | None
    ph: float | None
    temp_c: float | None


def level_pct(volume_l: float, capacity_l: float) -> float | None:
    """Level as % of usable volume, or None for a tank with no capacity."""
    if capacity_l <= 0.0:
        return None
    return 100.0 * volume_l / capacity_l


def refill_flow_lps(params: ReservoirParams) -> float:
    """Flow through the open refill valve, in L/s (config holds L/min)."""
    return params.refill_flow_lpm / _S_PER_MIN


def reservoir_from_readings(
    volume_l: float, ec: float, ph: float, temp_c: float, params: ReservoirParams
) -> ReservoirState:
    """Build a state from readings, for example a scenario's starting water."""
    _require_fits(volume_l, params)
    return ReservoirState(
        volume_l=volume_l,
        nutrient_g=nutrient_g_for_ec(ec, volume_l, params.ec_per_g_per_l),
        ph=ph,
        temp_c=temp_c,
    )


def readings(state: ReservoirState, params: ReservoirParams) -> ReservoirReadings:
    has_water = state.volume_l > 0.0
    return ReservoirReadings(
        volume_l=state.volume_l,
        level_pct=level_pct(state.volume_l, params.capacity_l),
        ec_ms_cm=ec_ms_cm(state.nutrient_g, state.volume_l, params.ec_per_g_per_l),
        ph=state.ph if has_water else None,
        temp_c=state.temp_c if has_water else None,
    )


def dose(state: ReservoirState, line: str, ml: float, params: ReservoirParams) -> ReservoirState:
    """Add ml of a dosing line's stock to the reservoir, all at once.

    The stock is taken as made up with raw water, so its own volume carries
    raw-water pH and temperature; its effect beyond that is the line's
    nutrient_g_per_ml and ph_change_per_ml_per_1000l. A dose into a full tank
    overflows the same volume of mixed solution.
    """
    require_non_negative("ml", ml)
    _require_fits(state.volume_l, params)
    try:
        effect = params.dose_lines[line]
    except KeyError:
        raise ValueError(
            f"unknown dosing line {line!r}; configured: {sorted(params.dose_lines)}"
        ) from None
    if ml == 0.0:
        return state

    raw = params.raw_water
    added_l = ml / ML_PER_L
    volume_l = state.volume_l + added_l
    nutrient_g = state.nutrient_g + ml * effect.nutrient_g_per_ml
    ph = mix(state.ph, state.volume_l, raw.ph, added_l)
    ph += ph_shift(ml, volume_l, effect.ph_change_per_ml_per_1000l)
    temp_c = mix(state.temp_c, state.volume_l, raw.temp_c, added_l)
    volume_l, nutrient_g = _spill_over(volume_l, nutrient_g, params.capacity_l)
    return ReservoirState(volume_l, nutrient_g, clamp_ph(ph), temp_c)


def step_reservoir(
    state: ReservoirState, dt_s: float, inputs: StepInputs, params: ReservoirParams
) -> ReservoirState:
    """Move the reservoir forward by dt_s simulated seconds."""
    require_non_negative("dt_s", dt_s)
    _require_fits(state.volume_l, params)
    if dt_s == 0.0:
        return state
    steps = min(params.max_substeps, max(1, math.ceil(dt_s / params.max_substep_s)))
    h_s = dt_s / steps
    for _ in range(steps):
        state = _substep(state, h_s, inputs, params)
    return state


def _substep(
    state: ReservoirState, h_s: float, inputs: StepInputs, params: ReservoirParams
) -> ReservoirState:
    raw = params.raw_water

    # 1. Refill: raw water mixes in, bringing its own salts, pH and heat.
    in_l = inputs.refill_lps * h_s
    volume_l = state.volume_l + in_l
    raw_g_per_l = nutrient_g_for_ec(raw.ec_ms_cm, 1.0, params.ec_per_g_per_l)
    nutrient_g = state.nutrient_g + in_l * raw_g_per_l
    ph = mix(state.ph, state.volume_l, raw.ph, in_l)
    temp_c = mix(state.temp_c, state.volume_l, raw.temp_c, in_l)

    # 2. Plants and leak take what they want, limited to what is there. When
    # the tank runs dry part-way through, both get the same share.
    has_water = volume_l > 0.0
    want_drink_l = inputs.plants.water_lps * h_s
    want_leak_l = inputs.leak_lps * h_s
    want_l = want_drink_l + want_leak_l
    served = 1.0 if want_l <= volume_l else volume_l / want_l
    leak_l = want_leak_l * served
    if leak_l > 0.0:
        # Leaking solution carries its dissolved nutrients; drinking does not.
        nutrient_g *= (volume_l - leak_l) / volume_l
    volume_l = max(0.0, volume_l - want_drink_l * served - leak_l)
    if has_water:
        nutrient_g -= min(nutrient_g, inputs.plants.nutrient_gps * h_s * served)

    # 3. Overflow above capacity.
    volume_l, nutrient_g = _spill_over(volume_l, nutrient_g, params.capacity_l)

    # 4. Heat exchange with the air, and 5. slow pH drift.
    temp_c = solution_temp_after(
        temp_c, inputs.air_temp_c, h_s, volume_l, params.air_coupling_w_per_k
    )
    if volume_l > 0.0:
        ph = relax_toward(
            ph, params.ph_drift_target, h_s, params.ph_drift_time_constant_h * _S_PER_H
        )
    return ReservoirState(volume_l, max(0.0, nutrient_g), clamp_ph(ph), temp_c)


def _spill_over(volume_l: float, nutrient_g: float, capacity_l: float) -> tuple[float, float]:
    """Cap volume at capacity; the spilled solution takes its share of nutrients."""
    if volume_l <= capacity_l:
        return volume_l, nutrient_g
    return capacity_l, nutrient_g * (capacity_l / volume_l)


def _require_fits(volume_l: float, params: ReservoirParams) -> None:
    if volume_l > params.capacity_l:
        raise ValueError(
            f"volume {volume_l} L exceeds the reservoir capacity {params.capacity_l} L"
        )
