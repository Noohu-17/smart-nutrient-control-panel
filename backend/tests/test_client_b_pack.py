"""client_B's config pack (T1-31): the back end reads it as it reads client_A's.

client_B is the prototype-extension rig farm, from the team's farm details form (R-1). These check
the pack's shape, not its numbers, which the team will keep adjusting.
"""

from pathlib import Path

from app.alerts import AlertRules
from app.api.pack import read_dosing, read_farm, site_of

CLIENT_B = Path(__file__).resolve().parents[2] / "config" / "clients" / "client_B"


def test_client_b_is_two_nft_zones_dosed_with_n_p_and_k():
    farm = read_farm(CLIENT_B)
    assert [(zone.id, zone.controller, zone.dose_lines) for zone in farm.zones] == [
        ("Z1", "ZC1", ["N", "P", "K"]),
        ("Z2", "ZC2", ["N", "P", "K"]),
    ]
    assert [zone.source for zone in farm.zones] == ["LIVE", "LIVE"]  # real hardware (T1-33, §2.4)
    assert farm.features["playground"] is False  # a real farm: no playground
    assert site_of(CLIENT_B).org_id == "client_B"


def test_every_client_b_zone_has_what_the_dose_review_needs():
    for zone in read_farm(CLIENT_B).zones:
        dosing = read_dosing(CLIENT_B, zone.id)
        assert dosing is not None and dosing.target is not None
        assert set(dosing.limits["maxPerDoseMl"]) == set(zone.dose_lines)
        assert set(dosing.pump_ml_per_min) >= set(zone.dose_lines)
    assert AlertRules.from_pack(CLIENT_B) is not None
