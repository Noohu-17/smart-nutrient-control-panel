"""Mixing and first-order relaxation: the two pieces of maths the model shares."""

from __future__ import annotations

import math


def mix(value_a: float, volume_a: float, value_b: float, volume_b: float) -> float:
    """Volume-weighted mean of an intensive property of two waters.

    Exact for temperature (same heat capacity per litre) and, under the linear
    buffer approximation in nutrients.py, for pH. If both volumes are zero there
    is nothing to mix and value_a is returned unchanged.
    """
    total = volume_a + volume_b
    if total <= 0.0:
        return value_a
    return (value_a * volume_a + value_b * volume_b) / total


def relax_toward(value: float, target: float, dt_s: float, time_constant_s: float) -> float:
    """Advance dx/dt = (target - x) / tau by dt_s seconds, exactly.

    Uses the closed-form solution, so it never overshoots the target however
    long the step: two half steps give the same result as one full step.
    An infinite time constant means no exchange at all.
    """
    if math.isinf(time_constant_s):
        return value
    return target + (value - target) * math.exp(-dt_s / time_constant_s)
