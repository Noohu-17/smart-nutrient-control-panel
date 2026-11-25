"""EC and pH of the nutrient solution.

EC
    EC = k * M / V (simulator guide section 5). M is the dissolved salt mass in
    grams, V the solution volume in litres, k (mS/cm per g/L) comes from
    simulator.json. EC is taken as linear in concentration, which is a fair
    approximation over the hydroponic working range (roughly 0.5-3 mS/cm). It
    overstates EC for near-undiluted stock, a state a reservoir only reaches
    when it is dosed while empty.

pH (provisional)
    pH is logarithmic, and how far acid moves it depends on the raw water's
    alkalinity (bicarbonate buffering). There are no titration data for the
    SIET water yet, so the model uses the simplest defensible approximation: a
    constant buffer capacity around the working range. Under it:
      * x mL of a line's stock in V litres shifts pH by c * x * 1000 / V, where
        c (pH per mL per 1000 L) is set per dosing line in simulator.json;
      * mixing two waters gives the volume-weighted mean pH (mixing.mix);
      * taking water out (plants, leak, overflow) leaves pH unchanged.
    Valid for small moves within about pH 5-7.5. Replace it with an
    alkalinity-based model once agronomy supplies titration data.
"""

from __future__ import annotations

from .params import PH_SCALE_MAX, PH_SCALE_MIN, require_non_negative, require_positive

ML_PER_L = 1000.0
_REFERENCE_VOLUME_L = 1000.0  # the "per 1000 L" in ph_change_per_ml_per_1000l


def ec_ms_cm(nutrient_g: float, volume_l: float, ec_per_g_per_l: float) -> float | None:
    """EC of the solution, or None when there is no solution to measure.

    None rather than 0.0: an empty tank has no EC, and a fake 0 could look like
    a weak solution that needs dosing (contract section 1: never a fake 0).
    """
    if volume_l <= 0.0:
        return None
    return ec_per_g_per_l * nutrient_g / volume_l


def nutrient_g_for_ec(ec: float, volume_l: float, ec_per_g_per_l: float) -> float:
    """Dissolved mass that gives this EC in this volume (inverse of ec_ms_cm)."""
    require_non_negative("ec", ec)
    require_non_negative("volume_l", volume_l)
    require_positive("ec_per_g_per_l", ec_per_g_per_l)
    return ec * volume_l / ec_per_g_per_l


def ph_shift(ml: float, volume_l: float, ph_change_per_ml_per_1000l: float) -> float:
    """pH change from ml of a line's stock once mixed into volume_l litres.

    volume_l is the volume after the stock has gone in, so it is never zero
    when ml is not.
    """
    if ml == 0.0:
        return 0.0
    require_positive("volume_l", volume_l)
    return ph_change_per_ml_per_1000l * ml * _REFERENCE_VOLUME_L / volume_l


def clamp_ph(ph: float) -> float:
    """Keep pH on the physical scale; the linear model can overshoot under huge doses."""
    return min(PH_SCALE_MAX, max(PH_SCALE_MIN, ph))
