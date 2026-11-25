"""Solution temperature: the reservoir drifts toward the air temperature.

Newton cooling with an exact exponential step, so long steps stay stable:
    tau = V * c_water / UA
where UA (W/K, simulator.json) is the solution's heat exchange with the air.
A half-full tank therefore follows the air twice as fast as a full one.
Chillers are not modelled yet.
"""

from __future__ import annotations

import math

from .mixing import relax_toward

# Volumetric heat capacity of water: 4.186 kJ/(kg K) x about 1 kg/L.
# A physical constant, not a farm value.
WATER_HEAT_CAPACITY_J_PER_L_K = 4186.0


def solution_time_constant_s(volume_l: float, air_coupling_w_per_k: float) -> float:
    """Seconds for the solution to close 63 % of its gap to the air temperature."""
    if air_coupling_w_per_k <= 0.0:
        return math.inf
    return volume_l * WATER_HEAT_CAPACITY_J_PER_L_K / air_coupling_w_per_k


def solution_temp_after(
    temp_c: float,
    air_temp_c: float,
    dt_s: float,
    volume_l: float,
    air_coupling_w_per_k: float,
) -> float:
    """Solution temperature after dt_s seconds of exchange with the air.

    An empty reservoir has no solution; its temperature is taken as the air's,
    so water that arrives later starts from a defined value.
    """
    if volume_l <= 0.0:
        return air_temp_c
    tau_s = solution_time_constant_s(volume_l, air_coupling_w_per_k)
    return relax_toward(temp_c, air_temp_c, dt_s, tau_s)
