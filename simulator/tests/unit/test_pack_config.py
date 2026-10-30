"""Reading the client config pack (T4-2: the site for every topic; T4-4: the device list)."""

from __future__ import annotations

import json

import pytest

from sim.config import load_devices, load_site
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
