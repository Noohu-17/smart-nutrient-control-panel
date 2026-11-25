"""EC and pH formulas (T3-1)."""

from __future__ import annotations

import pytest

from sim.model.nutrients import clamp_ph, ec_ms_cm, nutrient_g_for_ec, ph_shift


def test_ec_is_proportional_to_concentration():
    assert ec_ms_cm(600.0, 600.0, 1.0) == pytest.approx(1.0)
    assert ec_ms_cm(600.0, 300.0, 1.0) == pytest.approx(2.0)  # same mass, half the water
    assert ec_ms_cm(600.0, 600.0, 1.5) == pytest.approx(1.5)  # k scales it


def test_no_water_means_no_ec_rather_than_a_fake_zero():
    assert ec_ms_cm(50.0, 0.0, 1.0) is None


def test_nutrient_for_ec_inverts_ec():
    mass = nutrient_g_for_ec(1.62, 936.0, 1.2)
    assert ec_ms_cm(mass, 936.0, 1.2) == pytest.approx(1.62)


@pytest.mark.parametrize("args", [(-0.1, 100.0, 1.0), (1.0, -1.0, 1.0), (1.0, 100.0, 0.0)])
def test_nutrient_for_ec_rejects_bad_input(args):
    with pytest.raises(ValueError):
        nutrient_g_for_ec(*args)


def test_ph_shift_scales_with_dose_and_inverse_volume():
    assert ph_shift(30.0, 1000.0, -0.006) == pytest.approx(-0.18)
    assert ph_shift(60.0, 1000.0, -0.006) == pytest.approx(-0.36)
    assert ph_shift(30.0, 500.0, -0.006) == pytest.approx(-0.36)
    assert ph_shift(0.0, 0.0, -0.006) == 0.0


def test_ph_shift_needs_some_water_when_dosing():
    with pytest.raises(ValueError):
        ph_shift(10.0, 0.0, -0.006)


def test_clamp_keeps_ph_on_the_scale():
    assert clamp_ph(-3.0) == 0.0
    assert clamp_ph(15.0) == 14.0
    assert clamp_ph(6.2) == 6.2
