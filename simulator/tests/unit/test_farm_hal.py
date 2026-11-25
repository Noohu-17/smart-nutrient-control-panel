"""The HAL over the farm model, and pumps that really dose (T4-8)."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from sim.farm import load_farm
from sim.farm_hal import FarmHal
from sim.messages import zone_telemetry
from sim.model.water import ReservoirState
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
REAL = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


@pytest.fixture
def farm():
    farm = load_farm(CLIENT_A)
    farm.now = SIM_START
    return farm


@pytest.fixture
def hal(farm, tmp_path):
    return FarmHal(farm, farm.zones["Z1"], tmp_path / "nvs_ZC1.json", utc_now=lambda: REAL)


# --- sensors, inputs, outputs ---------------------------------------------------------------------


def test_sensors_read_what_telemetry_reports(farm, hal):
    readings = zone_telemetry(farm.zones["Z1"])["readings"]
    assert hal.read_sensor("EC") == (readings["ec"], "OK")
    assert hal.read_sensor("PH") == (readings["ph"], "OK")
    assert hal.read_sensor("RESERVOIR_PCT") == (78, "OK")
    assert hal.read_sensor("DAYTANK_ACID_PCT") == (81, "OK")
    assert hal.read_sensor("CIRC_FLOW") == (31.0, "OK")


def test_nothing_to_measure_reads_null_with_fault(farm, hal):
    farm.zones["Z1"].reservoir = ReservoirState(0.0, 0.0, 6.0, 20.0)
    assert hal.read_sensor("EC") == (None, "FAULT")
    assert hal.read_sensor("DAYTANK_BASE_PCT") == (None, "FAULT")  # not a line this zone has
    assert hal.read_sensor("NO_SUCH_SENSOR") == (None, "FAULT")


@pytest.mark.parametrize(
    ("changes", "inputs"),
    [
        ({}, {"ESTOP": False, "MODE_REMOTE": True, "MODE_MAINT": False, "LEAK": False}),
        ({"estop": True}, {"ESTOP": True}),
        ({"mode": "LOCAL"}, {"MODE_REMOTE": False, "MODE_MAINT": False}),
        ({"mode": "MAINT"}, {"MODE_REMOTE": False, "MODE_MAINT": True}),
        ({"leak": True}, {"LEAK": True}),
    ],
)
def test_inputs_are_the_panel(farm, hal, changes, inputs):
    for name, value in changes.items():
        setattr(farm.zones["Z1"], name, value)
    assert {name: hal.read_input(name) for name in inputs} == inputs


def test_outputs_switch_the_zones_equipment(farm, hal):
    outputs = farm.zones["Z1"].outputs
    hal.set_output("PUMP_ACID", True)
    hal.set_output("REFILL_VALVE", True)
    assert (outputs["Z1-DOSE-ACID"], outputs["Z1-REFILL"]) == ("ON", "OPEN")
    hal.set_output("PUMP_ACID", False)
    hal.set_output("REFILL_VALVE", False)
    assert (outputs["Z1-DOSE-ACID"], outputs["Z1-REFILL"]) == ("OFF", "CLOSED")
    assert farm.zones["Z2"].outputs["Z2-DOSE-ACID"] == "OFF"  # its own zone only


def test_an_output_the_zone_doesnt_have_is_an_error(hal):
    with pytest.raises(ValueError, match="PUMP_BASE"):
        hal.set_output("PUMP_BASE", True)


def test_the_flow_meter_counts_since_the_last_read(farm, hal):
    farm.zones["Z1"].flow_meter_ml["A"] = 12.5
    assert hal.take_flow_ml("FLOW_A") == 12.5
    assert hal.take_flow_ml("FLOW_A") == 0.0


def test_the_controllers_clock_is_the_farms_time_and_real_time_is_real(farm, hal):
    assert hal.now_ms() == round(SIM_START.timestamp() * 1000)
    farm.step(90.0, SIM_START + timedelta(seconds=90))
    assert hal.now_ms() == round((SIM_START + timedelta(seconds=90)).timestamp() * 1000)
    assert hal.utc_ms() == round(REAL.timestamp() * 1000)


def test_a_farm_with_no_time_yet_is_an_error(tmp_path):
    farm = load_farm(CLIENT_A)
    hal = FarmHal(farm, farm.zones["Z1"], tmp_path / "nvs.json")
    with pytest.raises(ValueError, match="farm.now"):
        hal.now_ms()


# --- nvs in the state folder ----------------------------------------------------------------------


def test_nvs_survives_a_restart_of_the_simulator(farm, tmp_path):
    path = tmp_path / "nvs_ZC1.json"
    FarmHal(farm, farm.zones["Z1"], path).nvs_write("journal", {"cmdId": "x", "state": "EXECUTING"})
    again = FarmHal(farm, farm.zones["Z1"], path)
    assert again.nvs_read("journal") == {"cmdId": "x", "state": "EXECUTING"}
    assert again.nvs_read("latches") is None
    assert not path.with_name(path.name + ".tmp").exists()  # never left half-written


def test_what_nvs_returns_cannot_change_what_it_holds(hal):
    hal.nvs_write("latches", {"items": ["LEAK"]})
    hal.nvs_read("latches")["items"].append("LOW_LOW")
    assert hal.nvs_read("latches") == {"items": ["LEAK"]}


@pytest.mark.parametrize("text", ["not json", "[1, 2]"])
def test_an_unreadable_nvs_file_starts_empty_and_says_so(farm, tmp_path, caplog, text):
    path = tmp_path / "nvs_ZC1.json"
    path.write_text(text, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="sim.hal"):
        hal = FarmHal(farm, farm.zones["Z1"], path)
    assert hal.nvs_read("journal") is None
    assert "empty nvs" in caplog.text


# --- pumps that really dose -----------------------------------------------------------------------


def test_a_running_pump_moves_stock_into_the_reservoir_and_ec_rises(farm):
    zone = farm.zones["Z1"]
    ec_before, tank_before = zone.water().ec_ms_cm, zone.day_tank_l["A"]
    zone.outputs["Z1-DOSE-A"] = "ON"
    farm.step(30.0, SIM_START + timedelta(seconds=30))
    assert zone.flow_meter_ml["A"] == pytest.approx(60.0)  # 2 mL/s in client_A's hardware.json
    assert zone.day_tank_l["A"] == pytest.approx(tank_before - 0.060)
    assert zone.water().ec_ms_cm > ec_before
    assert zone.flow_meter_ml["B"] == 0.0


def test_a_stopped_pump_moves_nothing(farm):
    zone = farm.zones["Z1"]
    before = zone.reservoir
    farm.step(30.0, SIM_START + timedelta(seconds=30))
    assert zone.flow_meter_ml == {"A": 0.0, "B": 0.0, "ACID": 0.0}
    assert zone.water().ec_ms_cm == pytest.approx(before.nutrient_g / before.volume_l)


def test_acid_lowers_ph(farm):
    zone = farm.zones["Z1"]
    ph_before = zone.water().ph
    zone.outputs["Z1-DOSE-ACID"] = "ON"
    farm.step(15.0, SIM_START + timedelta(seconds=15))
    assert zone.water().ph < ph_before


def test_a_day_tank_that_runs_dry_delivers_only_what_it_had(farm):
    zone = farm.zones["Z1"]
    zone.day_tank_l["B"] = 0.010  # 10 mL left
    zone.outputs["Z1-DOSE-B"] = "ON"
    farm.step(60.0, SIM_START + timedelta(seconds=60))
    assert zone.flow_meter_ml["B"] == pytest.approx(10.0)
    assert zone.day_tank_l["B"] == pytest.approx(0.0)


def test_the_farm_keeps_its_own_time(farm):
    farm.step(10.0, SIM_START + timedelta(seconds=10))
    assert farm.now == SIM_START + timedelta(seconds=10)


def edited_hardware(tmp_path, edit):
    client_dir = shutil.copytree(CLIENT_A, tmp_path / "client")
    path = client_dir / "hardware.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return client_dir


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["pumps"]["doseLines"].pop("ACID"),
        lambda d: d["pumps"]["doseLines"]["A"].update(flowMlPerS=0),
        lambda d: d["pumps"]["doseLines"]["B"].update(flowMlPerS="fast"),
        lambda d: d.pop("pumps"),
    ],
)
def test_every_line_needs_a_pump_rate_in_hardware_json(tmp_path, edit):
    with pytest.raises(ValueError, match="flowMlPerS"):
        load_farm(edited_hardware(tmp_path, edit))


def test_pump_rates_come_from_the_config_not_code(tmp_path):
    def faster(data):
        data["pumps"]["doseLines"]["A"]["flowMlPerS"] = 5.5

    zone = load_farm(edited_hardware(tmp_path, faster)).zones["Z1"]
    assert zone.dose_pump_ml_per_s == {"A": 5.5, "B": 2.0, "ACID": 2.0}


def test_a_zone_with_its_own_pump_rates_runs_at_them(tmp_path):
    """T1-31: hardware.json pumps.zones.<zone>.doseLines stands over pumps.doseLines for that zone
    only, line by line."""
    def zone_b_faster(data):
        data["pumps"]["zones"] = {"Z2": {"doseLines": {"A": {"flowMlPerS": 16.667}}}}

    zones = load_farm(edited_hardware(tmp_path, zone_b_faster)).zones
    assert zones["Z1"].dose_pump_ml_per_s == {"A": 2.0, "B": 2.0, "ACID": 2.0}
    assert zones["Z2"].dose_pump_ml_per_s == {"A": 16.667, "B": 2.0, "ACID": 2.0}


def test_a_zones_own_pump_rate_must_be_a_rate(tmp_path):
    def broken(data):
        data["pumps"]["zones"] = {"Z2": {"doseLines": {"B": {"flowMlPerS": 0}}}}

    message = r"pumps\.zones\.Z2\.doseLines\.B\.flowMlPerS must be a number > 0"
    with pytest.raises(ValueError, match=message):
        load_farm(edited_hardware(tmp_path, broken))
