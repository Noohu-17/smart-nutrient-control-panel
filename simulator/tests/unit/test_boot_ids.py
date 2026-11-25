"""bootId goes up by 1 on every simulator start and survives restarts (T4-2)."""

from __future__ import annotations

import json

from sim.state import next_boot_ids


def test_first_start_is_boot_1_then_each_start_adds_1(tmp_path):
    state = tmp_path / ".state" / "boot_ids.json"
    assert next_boot_ids(state, ["GW1", "ZC1"]) == {"GW1": 1, "ZC1": 1}
    assert next_boot_ids(state, ["GW1", "ZC1"]) == {"GW1": 2, "ZC1": 2}
    assert json.loads(state.read_text()) == {"GW1": 2, "ZC1": 2}


def test_a_new_device_starts_at_1_and_others_are_kept(tmp_path):
    state = tmp_path / "boot_ids.json"
    next_boot_ids(state, ["ZC1", "ZC2"])
    assert next_boot_ids(state, ["ZC1", "PM1"]) == {"ZC1": 2, "PM1": 1}
    assert json.loads(state.read_text())["ZC2"] == 1  # a device not played now keeps its count


def test_unreadable_state_restarts_at_1_with_a_warning(tmp_path, caplog):
    state = tmp_path / "boot_ids.json"
    state.write_text("{not json")
    assert next_boot_ids(state, ["ZC1"]) == {"ZC1": 1}
    assert "unreadable" in caplog.text


def test_bad_values_in_the_state_file_are_ignored(tmp_path):
    state = tmp_path / "boot_ids.json"
    state.write_text(json.dumps({"ZC1": "7", "ZC2": True, "GW1": -3, "PM1": 4}))
    assert next_boot_ids(state, ["ZC1", "ZC2", "GW1", "PM1"]) == {
        "ZC1": 1, "ZC2": 1, "GW1": 1, "PM1": 5,
    }
