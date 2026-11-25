"""Solution temperature, mixing and relaxation maths (T3-1)."""

from __future__ import annotations

import math

import pytest

from sim.model.climate import (
    WATER_HEAT_CAPACITY_J_PER_L_K,
    solution_temp_after,
    solution_time_constant_s,
)
from sim.model.mixing import mix, relax_toward


def test_time_constant_grows_with_volume():
    assert solution_time_constant_s(1000.0, 232.5) == pytest.approx(
        1000.0 * WATER_HEAT_CAPACITY_J_PER_L_K / 232.5
    )
    assert solution_time_constant_s(500.0, 232.5) == pytest.approx(
        solution_time_constant_s(1000.0, 232.5) / 2.0
    )


def test_no_air_coupling_means_no_temperature_change():
    assert solution_time_constant_s(1000.0, 0.0) == math.inf
    assert solution_temp_after(20.0, 35.0, 3600.0, 1000.0, 0.0) == 20.0


def test_cooling_works_like_warming():
    after = solution_temp_after(30.0, 20.0, 3600.0, 1000.0, 232.5)
    assert 20.0 < after < 30.0


def test_empty_tank_takes_the_air_temperature():
    assert solution_temp_after(20.0, 31.0, 1.0, 0.0, 232.5) == 31.0


def test_relaxation_is_exact_so_step_size_does_not_matter():
    one_step = relax_toward(20.0, 30.0, 3600.0, 18000.0)
    two_steps = relax_toward(relax_toward(20.0, 30.0, 1800.0, 18000.0), 30.0, 1800.0, 18000.0)
    assert one_step == pytest.approx(two_steps)


def test_relaxation_never_overshoots():
    assert relax_toward(20.0, 30.0, 1e12, 1.0) == 30.0


def test_mixing_is_volume_weighted():
    assert mix(20.0, 300.0, 30.0, 100.0) == pytest.approx(22.5)
    assert mix(20.0, 0.0, 30.0, 100.0) == 30.0
    assert mix(20.0, 0.0, 30.0, 0.0) == 20.0  # nothing to mix
