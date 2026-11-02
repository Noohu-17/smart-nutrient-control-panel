"""ZONE_TELEMETRY, CLIMATE_TELEMETRY and POWER_STATE field for field (T4-5).

The expected values are the contract's own examples, read from docs/contract.md, because the
starting values in client_A's simulator.json are those examples. Every message is also checked
against Track 1's models (T1-7), for the shipped pack and for other equipment layouts.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime

import pytest
from app.contract import parse_farm_message

from sim.farm import load_farm
from sim.messages import (
    Envelope,
    climate_telemetry,
    climate_telemetry_topic,
    power_state,
    power_state_topic,
    zone_telemetry,
    zone_telemetry_topic,
)
from sim.model.water import ReservoirState
from sim.settings import REPO_ROOT, load_settings

T0 = datetime(2026, 11, 9, 4, 54, tzinfo=UTC)
CLIENT_A = load_settings({}).client_dir
PAYLOAD_LIMIT_BYTES = 16 * 1024  # contract 3


def contract_example(heading: str) -> dict:
    """The first JSON example under a heading of docs/contract.md."""
    text = (REPO_ROOT / "docs" / "contract.md").read_text(encoding="utf-8")
    after_heading = text.split(f"### {heading}", 1)[1]
    return json.loads(after_heading.split("```json", 1)[1].split("```", 1)[0])


def validated(msg_type: str, device: str, body: dict) -> dict:
    """The full message, after Track 1's model has accepted it."""
    message = {**Envelope("siet-ph1", device, 1).next(msg_type, T0, T0), **body}
    parse_farm_message(json.loads(json.dumps(message)))
    return message


def edited_pack(tmp_path, file_name: str, edit) -> object:
    """A copy of client_A with one file changed by edit(data)."""
    client_dir = shutil.copytree(CLIENT_A, tmp_path / "client")
    path = client_dir / file_name
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return client_dir


@pytest.fixture
def farm():
    return load_farm(CLIENT_A)


# --- the contract's own examples ------------------------------------------------------


def test_zone_telemetry_matches_the_contract_example(farm):
    message = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(farm.zones["Z1"]))
    example = contract_example("6.1")
    for field in ("zone", "readings", "quality", "equipment", "mode", "estop", "leak",
                  "activeCmdId"):
        assert message[field] == example[field], field


def test_climate_telemetry_matches_the_contract_example(farm):
    message = validated("CLIMATE_TELEMETRY", "CC1", climate_telemetry(farm.climate))
    example = contract_example("6.2")
    for field in ("readings", "quality", "setpoints"):
        assert message[field] == example[field], field
    assert set(message["equipment"]) == set(example["equipment"])
    assert set(message["runTimeTodayMin"]) == set(example["runTimeTodayMin"])


def test_climate_starts_with_nothing_running_and_in_manual_as_phase_a_says(farm):
    message = climate_telemetry(farm.climate)
    equipment = message["equipment"]
    assert (message["mode"], message["manualUntil"]) == ("MANUAL", None)  # contract 7.5
    assert equipment["fanStage"] == 0
    assert {equipment[fan] for fan in ("FAN1", "FAN2", "FAN3", "FAN4")} == {"OFF"}
    assert (equipment["PAD1"], equipment["HAF1"]) == ("OFF", "OFF")
    assert equipment["CH1"] == {"state": "OFF", "setpointC": 22.0}
    assert equipment["FOG1"] == "NOT_INSTALLED"
    assert message["runTimeTodayMin"] == {"FANS": 0, "PAD1": 0}


def test_power_state_matches_the_contract_example(farm):
    message = validated("POWER_STATE", "PM1", power_state(farm.power))
    example = contract_example("6.3")
    for field in ("mains", "phases", "source", "generator", "ats", "ups"):
        assert message[field] == example[field], field


def test_topics_are_the_contract_topics():
    assert zone_telemetry_topic("siet-ph1", "Z2") == "farm/siet-ph1/zone/Z2/telemetry"
    assert climate_telemetry_topic("siet-ph1") == "farm/siet-ph1/climate/telemetry"
    assert power_state_topic("siet-ph1") == "farm/siet-ph1/power/state"


def test_every_message_fits_the_payload_limit(farm):
    bodies = (zone_telemetry(farm.zones["Z1"]), climate_telemetry(farm.climate),
              power_state(farm.power))
    for body in bodies:
        assert len(json.dumps(body).encode()) < PAYLOAD_LIMIT_BYTES


# --- precision, nulls and quality -----------------------------------------------------


def test_readings_go_out_at_the_contract_precision(farm):
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(volume_l=936.4, nutrient_g=1516.8, ph=6.0449, temp_c=23.44)
    zone.day_tank_l["A"] = 32.26
    readings = zone_telemetry(zone)["readings"]
    assert (readings["reservoirL"], readings["reservoirPct"]) == (936, 78)
    assert (readings["ec"], readings["ph"], readings["solTempC"]) == (1.62, 6.04, 23.4)
    assert (readings["dayTankPct"]["A"], readings["dayTankL"]["A"]) == (65, 32.3)
    assert isinstance(readings["reservoirL"], int) and isinstance(readings["reservoirPct"], int)


def test_an_empty_reservoir_reports_null_with_fault_and_never_a_fake_zero(farm):
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(volume_l=0.0, nutrient_g=5.0, ph=6.0, temp_c=20.0)
    message = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(zone))
    for name in ("ec", "ph", "solTempC"):
        assert message["readings"][name] is None
        assert message["quality"][name] == "FAULT"
    assert (message["readings"]["reservoirPct"], message["quality"]["reservoirPct"]) == (0, "OK")


def test_a_tank_with_no_capacity_has_no_level(tmp_path):
    def no_capacity(layout):
        layout["zones"][0]["reservoir"]["usableL"] = 0

    def nothing_in_it(simulator):
        simulator["model"]["initialState"]["reservoirPct"] = 0

    client_dir = edited_pack(tmp_path, "layout.json", no_capacity)
    path = client_dir / "simulator.json"
    simulator = json.loads(path.read_text(encoding="utf-8"))
    nothing_in_it(simulator)
    path.write_text(json.dumps(simulator), encoding="utf-8")
    message = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(load_farm(client_dir).zones["Z1"]))
    assert message["readings"]["reservoirPct"] is None
    assert message["quality"]["reservoirPct"] == "FAULT"


def test_a_stopped_circulation_pump_reads_a_real_zero_flow(farm):
    zone = farm.zones["Z1"]
    zone.outputs["Z1-CIRC"] = "OFF"
    message = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(zone))
    assert (message["readings"]["circFlowLpm"], message["quality"]["circFlowLpm"]) == (0.0, "OK")


def test_dissolved_oxygen_not_fitted_is_null_with_no_quality(farm):
    message = zone_telemetry(farm.zones["Z1"])
    assert message["readings"]["doMgL"] is None
    assert "doMgL" not in message["quality"]


def test_dissolved_oxygen_fitted_has_no_model_yet_so_it_reports_fault_not_a_number(tmp_path):
    def fit_it(layout):
        layout["zones"][0]["sensors"]["dissolvedOxygen"] = True

    farm = load_farm(edited_pack(tmp_path, "layout.json", fit_it))
    message = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(farm.zones["Z1"]))
    assert message["readings"]["doMgL"] is None
    assert message["quality"]["doMgL"] == "FAULT"


# --- other farms: nothing about the equipment is fixed in code ------------------------


def test_equipment_and_lines_follow_the_layout_for_any_zone_and_fan_count(tmp_path):
    def other_farm(layout):
        zone = layout["zones"][0]
        zone["doseLines"] = ["A", "ACID"]
        zone["dayTanks"] = {"A": {"usableL": 80}, "ACID": {"usableL": 20}}
        layout["zones"] = [zone]  # one zone only (contract 2.1)
        layout["climate"]["fans"] = [f"FAN{n}" for n in range(1, 7)]
        layout["climate"]["chillers"] = {}
        layout["climate"]["shade"] = []
        layout["climate"]["foggers"] = ["FOG1"]
        layout["climate"]["airSensors"] = ["MIDDLE"]

    farm = load_farm(edited_pack(tmp_path, "layout.json", other_farm))
    assert list(farm.zones) == ["Z1"]
    zone = validated("ZONE_TELEMETRY", "ZC1", zone_telemetry(farm.zones["Z1"]))
    assert set(zone["equipment"]) == {"Z1-CIRC", "Z1-REFILL", "Z1-DOSE-A", "Z1-DOSE-ACID"}
    assert zone["readings"]["dayTankPct"] == {"A": 64, "ACID": 81}
    assert zone["readings"]["dayTankL"] == {"A": 51.2, "ACID": 16.2}

    climate = validated("CLIMATE_TELEMETRY", "CC1", climate_telemetry(farm.climate))
    assert set(climate["equipment"]) == {"fanStage", "FAN1", "FAN2", "FAN3", "FAN4", "FAN5",
                                         "FAN6", "PAD1", "HAF1", "FOG1"}
    assert climate["equipment"]["FOG1"] == "OFF"  # installed here, so no longer NOT_INSTALLED
    assert climate["readings"]["airTempByPosC"] == {"MIDDLE": 31.0}
    assert climate["readings"]["airTempC"] == 31.0  # the average of however many positions


def test_three_zones_each_get_their_own_ids(tmp_path):
    def three_zones(layout):
        third = json.loads(json.dumps(layout["zones"][1]))
        third.update(id="Z3", controller="ZC3")
        layout["zones"].append(third)

    farm = load_farm(edited_pack(tmp_path, "layout.json", three_zones))
    assert list(farm.zones) == ["Z1", "Z2", "Z3"]
    assert validated("ZONE_TELEMETRY", "ZC3", zone_telemetry(farm.zones["Z3"]))["zone"] == "Z3"
    assert "Z3-CIRC" in zone_telemetry(farm.zones["Z3"])["equipment"]


def test_no_generator_or_ups_in_the_layout_leaves_those_fields_out(tmp_path):
    def mains_only(layout):
        layout["power"] = {"monitor": "PM1", "generator": False, "ups": False}

    farm = load_farm(edited_pack(tmp_path, "layout.json", mains_only))
    message = validated("POWER_STATE", "PM1", power_state(farm.power))
    assert "generator" not in message and "ups" not in message
    assert message["ats"] == "MAINS"


def test_climate_automatic_on_starts_in_automatic(tmp_path):
    def switch_on(features):
        features["climateAutomatic"] = True

    farm = load_farm(edited_pack(tmp_path, "features.json", switch_on))
    assert validated("CLIMATE_TELEMETRY", "CC1", climate_telemetry(farm.climate))["mode"] == (
        "AUTOMATIC")


def test_manual_until_is_contract_time(farm):
    farm.climate.manual_until = T0
    message = validated("CLIMATE_TELEMETRY", "CC1", climate_telemetry(farm.climate))
    assert message["manualUntil"] == "2026-11-09T04:54:00.000Z"
