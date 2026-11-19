"""Reading the client config pack (T4-2: the site for every topic; T4-4: the device list)."""

from __future__ import annotations

import json

import pytest

from sim.config import load_devices, load_site, simulated_devices, zone_sources
from sim.settings import load_settings


def write_layout(tmp_path, layout):
    (tmp_path / "layout.json").write_text(json.dumps(layout))
    return tmp_path


def test_site_comes_from_layout_json(tmp_path):
    assert load_site(write_layout(tmp_path, {"site": "siet-ph1"})) == "siet-ph1"


def test_the_real_client_a_pack_gives_siet_ph1():
    assert load_site(load_settings({}).client_dir) == "siet-ph1"


@pytest.mark.parametrize("site", [None, "", "SIET PH1", "siet/ph1", 7])
def test_rejects_a_site_that_cannot_go_in_a_topic(tmp_path, site):
    with pytest.raises(ValueError, match="site"):
        load_site(write_layout(tmp_path, {"site": site}))


def test_missing_pack_file_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_site(tmp_path)


def zoned_pack(tmp_path, *, playground, z1=None, z2=None):
    """Two zones, Z1 and Z2, with the sources given (None: left out of layout.json)."""
    zones = [{"id": z, "name": z, "controller": f"ZC{n}", **({"source": s} if s else {})}
             for n, (z, s) in enumerate((("Z1", z1), ("Z2", z2)), start=1)]
    devices = [{"id": "GW1", "kind": "GATEWAY"},
               {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"},
               {"id": "ZC2", "kind": "ZONE_CONTROLLER", "zone": "Z2"}]
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_layout(tmp_path, {"site": "s", "zones": zones, "devices": devices})
    (tmp_path / "features.json").write_text(json.dumps({"playground": playground}))
    return tmp_path


def test_a_zone_left_without_a_source_is_simulated_on_a_playground_and_live_elsewhere(tmp_path):
    """Contract 2.4 (T1-33): a real farm's zone is never simulated by mistake."""
    assert zone_sources(zoned_pack(tmp_path / "play", playground=True)) == {
        "Z1": "SIMULATED", "Z2": "SIMULATED"}
    assert zone_sources(zoned_pack(tmp_path / "farm", playground=False)) == {
        "Z1": "LIVE", "Z2": "LIVE"}
    assert zone_sources(zoned_pack(tmp_path / "mixed", playground=True, z1="LIVE")) == {
        "Z1": "LIVE", "Z2": "SIMULATED"}
    with pytest.raises(ValueError, match="source must be LIVE or SIMULATED"):
        zone_sources(zoned_pack(tmp_path / "bad", playground=True, z2="REAL"))


def test_the_simulator_never_plays_a_live_zones_controller(tmp_path):
    """Its hardware, or the zone controller service on the rig, plays it (T1-33)."""
    pack = zoned_pack(tmp_path, playground=True, z1="LIVE")
    assert [d["id"] for d in simulated_devices(pack)] == ["GW1", "ZC2"]
    assert [d["id"] for d in load_devices(pack)] == ["GW1", "ZC1", "ZC2"]  # the layout's list


def test_client_a_simulates_both_its_zones():
    client_a = load_settings({}).client_dir
    assert zone_sources(client_a) == {"Z1": "SIMULATED", "Z2": "SIMULATED"}
    assert len(simulated_devices(client_a)) == len(load_devices(client_a))


def test_client_and_state_dirs_can_be_moved(tmp_path):
    settings = load_settings({"SIM_CLIENT_DIR": str(tmp_path), "SIM_STATE_DIR": "custom-state"})
    assert settings.client_dir == tmp_path
    assert settings.state_dir.name == "custom-state"  # relative paths start at simulator/


CLIENT_A_DEVICES = [
    {"id": "GW1", "kind": "GATEWAY"},
    {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"},
    {"id": "ZC2", "kind": "ZONE_CONTROLLER", "zone": "Z2"},
    {"id": "CC1", "kind": "CLIMATE_CONTROLLER"},
    {"id": "PM1", "kind": "POWER_MONITOR"},
]


def test_the_real_client_a_pack_lists_its_five_devices_in_file_order():
    assert list(load_devices(load_settings({}).client_dir)) == CLIENT_A_DEVICES


def test_removing_zc2_from_the_layout_removes_that_device(tmp_path):
    layout = json.loads((load_settings({}).client_dir / "layout.json").read_text(encoding="utf-8"))
    layout["devices"] = [d for d in layout["devices"] if d["id"] != "ZC2"]
    devices = load_devices(write_layout(tmp_path, layout))
    assert [d["id"] for d in devices] == ["GW1", "ZC1", "CC1", "PM1"]


def test_a_device_added_to_the_layout_is_played_too(tmp_path):
    devices = load_devices(write_layout(tmp_path, {"devices": [
        *CLIENT_A_DEVICES, {"id": "ZC3", "kind": "ZONE_CONTROLLER", "zone": "Z3"},
    ]}))
    assert devices[-1] == {"id": "ZC3", "kind": "ZONE_CONTROLLER", "zone": "Z3"}


@pytest.mark.parametrize(
    "devices",
    [
        None,
        [],
        {"GW1": "GATEWAY"},
        ["GW1"],
        [{"kind": "GATEWAY"}],
        [{"id": "gw1", "kind": "GATEWAY"}],
        [{"id": "ZC1/x", "kind": "ZONE_CONTROLLER"}],
        [{"id": 1, "kind": "GATEWAY"}],
        [{"id": "GW1"}],
        [{"id": "GW1", "kind": "ROUTER"}],
        [{"id": "ZC1", "kind": "ZONE_CONTROLLER"}, {"id": "ZC1", "kind": "ZONE_CONTROLLER"}],
    ],
)
def test_rejects_a_device_list_the_simulator_cannot_play(tmp_path, devices):
    with pytest.raises(ValueError, match="device"):
        load_devices(write_layout(tmp_path, {"devices": devices}))
