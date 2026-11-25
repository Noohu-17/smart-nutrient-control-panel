"""What the config pack says each zone, crop and dose line is (T1-38), and how the zone and dosing
APIs answer it.

The pack tests read client_A's and client_B's own files and need no database. The API tests seed a
copy of client_A into a throwaway database, as the other read API tests do.
"""

import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from test_read_api import api_client, contract_zone_example

from app.api.pack import read_dosing, read_zone_details
from app.api.routes import _zone_details
from app.db.seed import seed
from app.main import app
from app.packs import FolderPack

CLIENTS = Path(__file__).resolve().parents[2] / "config" / "clients"
CLIENT_A, CLIENT_B = CLIENTS / "client_A", CLIENTS / "client_B"


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


def as_sent(model) -> dict:
    return model.model_dump(mode="json", exclude_unset=True)


# --- The config pack ------------------------------------------------------------------------


def test_client_a_zone_b_is_the_contracts_example():
    """§11.2's zone example is client_A's Zone B, so the two can't drift apart."""
    example = contract_zone_example()
    profile, crop = read_zone_details(CLIENT_A)["Z2"]
    assert as_sent(profile) == example["profile"]
    assert as_sent(crop) == example["crop"]


def test_client_b_is_two_nft_zones_with_their_tanks_sensors_and_links():
    details = read_zone_details(CLIENT_B)
    assert list(details) == ["Z1", "Z2"]
    z1, _ = details["Z1"]
    assert (z1.growing_system, z1.plant_sites) == ("NFT", 4 * 45)
    assert (z1.tank.volume_l, z1.tank.usable_l) == (90, 90)  # the 90 L main tank, all usable
    assert z1.sensors["ec"] and not z1.sensors["dayTankLevel"]  # the N, P, K tanks have no level sensors
    # both run on the rig (io_map.json): Zone A on its Zone 1, Zone B on its Zone 2 (T1-31)
    assert [details[zone][0].link for zone in details] == ["SF_GATEWAY", "SF_GATEWAY"]


def test_client_b_crops_have_their_variety_and_planting_day():
    crops = {zone: crop for zone, (_, crop) in read_zone_details(CLIENT_B).items()}
    assert [(c.crop, c.name, c.variety, c.stage) for c in crops.values()] == [
        ("LETTUCE", "Lettuce", "Grand Rapids", "SEEDLING"),
        ("SPINACH", "Spinach", "Ooty 1", "SEEDLING"),
    ]
    assert all(isinstance(c.planted_on, date) for c in crops.values())


def test_client_b_crops_carry_their_stages_targets_and_the_probes_ppm_scale():
    """Test 22: Zone A's lettuce seedlings, EC 0.8–1.2 mS/cm (400–600 ppm on the rig's 0.5 TDS
    scale) and pH 5.8–6.2, from the farm details form."""
    details = read_zone_details(CLIENT_B)
    profile, crop = details["Z1"]
    assert as_sent(crop)["ecTarget"] == {"min": 0.8, "max": 1.2}
    assert as_sent(crop)["phTarget"] == {"min": 5.8, "max": 6.2}
    assert profile.tds_ppm_per_ms_cm == 500
    # a zone on an MQTT controller reports EC itself: no ppm scale
    assert read_zone_details(CLIENT_A)["Z1"][0].tds_ppm_per_ms_cm is None


def test_client_b_dose_lines_say_what_they_dose():
    stock = read_dosing(CLIENT_B, "Z1").stock
    assert [(line, s.compound, s.strength_pct, s.drum.volume_l) for line, s in stock.items()] == [
        ("A", "City Greens Grow 24-00-00, N", 24, 10),
        ("B", "City Greens Flora 05-26-00, P", 26, 10),
        ("C", "City Greens Thrive 06-00-19, K", 19, 10),
    ]


def test_a_zone_with_no_recipe_has_no_crop(tmp_path):
    pack = shutil.copytree(CLIENT_A, tmp_path / "client_A")
    recipes = json.loads((pack / "recipes.json").read_text(encoding="utf-8"))
    del recipes["zones"]["Z1"]
    (pack / "recipes.json").write_text(json.dumps(recipes), encoding="utf-8")
    details = read_zone_details(pack)
    assert details["Z1"][1] is None and details["Z2"][1] is not None


# --- GET /api/zones (§11.2) ----------------------------------------------------------------


def test_every_zone_answer_carries_its_profile_and_crop(tmp_path, migrated_db):
    pack = shutil.copytree(CLIENT_A, tmp_path / "client_A")
    seed(migrated_db, pack)
    api = api_client(pack, migrated_db)
    details = read_zone_details(pack)

    zones = api.get("/api/zones").json()["items"]
    assert [zone["zone"] for zone in zones] == ["Z1", "Z2"]
    for zone in zones:
        profile, crop = details[zone["zone"]]
        assert (zone["profile"], zone["crop"]) == (as_sent(profile), as_sent(crop))
    one = api.get("/api/zones/Z2").json()
    assert one["profile"]["growingUnits"][0] == {
        "type": "NFT_BENCH", "count": 4, "channels": 6, "lengthM": 8.5, "plantSitesEach": 252,
    }
    assert one["crop"]["plantedOn"] == "2026-11-08"


def broken_pack(tmp_path, name):
    pack = shutil.copytree(CLIENT_A, tmp_path / "client_A")
    (pack / name).write_text("{ not json", encoding="utf-8")
    return pack


@pytest.mark.parametrize("name", ["recipes.json", "io_map.json"])
def test_a_broken_recipes_or_io_map_leaves_out_the_details_and_says_why(tmp_path, caplog, name):
    assert _zone_details(FolderPack(broken_pack(tmp_path, name))) == {}
    assert any("cannot load the config pack" in m for m in caplog.messages)
    assert any("without profile and crop" in m for m in caplog.messages)


def test_the_zones_still_answer_when_recipes_json_is_broken(tmp_path, migrated_db):
    pack = broken_pack(tmp_path, "recipes.json")
    seed(migrated_db, pack)
    api = api_client(pack, migrated_db)
    response = api.get("/api/zones")
    assert response.status_code == 200
    zones = response.json()["items"]
    assert [zone["zone"] for zone in zones] == ["Z1", "Z2"]
    assert not any("profile" in zone or "crop" in zone for zone in zones)
    one = api.get("/api/zones/Z1")
    assert one.status_code == 200 and "profile" not in one.json()
