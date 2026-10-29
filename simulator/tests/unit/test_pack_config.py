"""Reading the client config pack (T4-2: the site for every topic)."""

from __future__ import annotations

import json

import pytest

from sim.config import load_site
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
