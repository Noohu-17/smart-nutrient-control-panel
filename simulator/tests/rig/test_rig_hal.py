"""The rig's HAL (T1-32): readings in contract units and quality, equipment, and the config."""

from __future__ import annotations

import json

import pytest
from rig_support import RIG_ENTRY, make_rig_pack, zone_doc

from sim.rig.config import load_rig_settings, load_rig_zones
from sim.rig.sf import dose_command, dose_id_for, status_of, subtopic, switch_off_command


def test_readings_are_the_zone_update_in_contract_units(rig):
    readings, quality = rig.hal.readings()
    assert readings == {
        "ec": 1.3,  # 650 ppm on the 500 scale
        "ph": 6.1, "solTempC": 24.5, "reservoirPct": 62, "reservoirL": 560,
        "circFlowLpm": None, "doMgL": None,
        "dayTankPct": {"A": None, "B": None, "ACID": None},
        "dayTankL": {"A": None, "B": None, "ACID": None},
    }
    assert quality == {"ec": "OK", "ph": "OK", "solTempC": "OK", "reservoirPct": "OK"}


@pytest.mark.parametrize(
    ("parts", "name", "expected"),
    [
        ({"water_quality": {"ph": None}}, "ph", (None, "FAULT")),  # no signal: null, never a 0
        ({"water_quality": {"tds_ppm": None}}, "ec", (None, "FAULT")),
        ({"water_quality": {"age_s": 45}}, "ph", (6.1, "STALE")),  # the rig's own 30 s rule
        ({"tank": {"sensor_ok": False, "level_pct": None}}, "reservoirPct", (None, "FAULT")),
        ({"water_quality": {"ph": 14.6}}, "ph", (14.6, "OUT_OF_RANGE")),
        ({"water_quality": None}, "solTempC", (None, "FAULT")),  # not heard from yet
    ],
)
def test_quality_says_what_the_rig_could_measure(rig, parts, name, expected):
    readings, quality = rig.hal.readings(zone_doc(ts=int(rig.clock.unix), **parts))
    assert (readings[name], quality[name]) == expected


def test_an_update_over_120_s_old_is_stale_for_dosing(rig):
    old = zone_doc(ts=int(rig.clock.unix) - 121)
    assert rig.hal.readings(old)[1]["ec"] == "STALE"
    rig.hal.update(zone_doc(ts=int(rig.clock.unix)))
    rig.clock.advance(121)  # nothing new since
    assert rig.hal.read_sensor("PH") == (6.1, "STALE")


def test_equipment_is_the_rigs_relays(rig):
    document = zone_doc(ts=int(rig.clock.unix),
                        actuators={"relays": {"relay1": True, "relay2": False, "relay3": False,
                                              "relay4": False}},
                        tank={"inlet_on": True})
    assert rig.hal.equipment(document) == {"Z1-DOSE-A": "ON", "Z1-DOSE-B": "OFF",
                                           "Z1-DOSE-ACID": "OFF", "Z1-REFILL": "OPEN"}
    assert "Z1-CIRC" not in rig.hal.equipment(document)  # no circulation pump on the rig


def test_the_panel_inputs(rig):
    assert rig.hal.read_input("MODE_REMOTE") and not rig.hal.read_input("MODE_MAINT")
    rig.hal.update(zone_doc(ts=int(rig.clock.unix), seq=43, actuators={"estop": True}))
    assert rig.hal.read_input("ESTOP")


def test_a_relay_is_only_switched_off_when_a_dose_runs_on_it(rig):
    rig.hal.set_output("PUMP_A", False)
    rig.hal.set_output("CIRC_PUMP", True)  # the rig's circulation isn't the controller's
    assert rig.sent == []
    with pytest.raises(ValueError):
        rig.hal.set_output("REFILL_VALVE", True)


def test_a_dose_outside_the_rigs_0_1_to_600_s_is_not_sent(rig):
    rig.hal.run_pump_for("PUMP_A", 1300, "too-long/A")  # 650 s at 2 mL/s
    assert rig.sent == [] and not rig.hal.pump_running("PUMP_A")


def test_a_duplicate_answer_means_the_rig_already_has_the_dose(rig):
    """A command sent again (or re-delivered after a gateway restart) is answered "duplicate":
    the dose is still the rig's own, and its sf/dose/1 reports say how it went."""
    rig.hal.run_pump_for("PUMP_A", 20, "again/A")
    rig.hal.run_pump_for("PUMP_B", 20, "busy/B")
    dose_a, dose_b = rig.sent
    result = {"schema": "sf/cmd-result/1", "gateway": "gateway-01", "result": None}
    rig.hal.on_cmd_result({**result, "id": dose_a["id"], "status": "duplicate"})
    rig.hal.on_cmd_result({**result, "id": dose_b["id"], "status": "busy"})
    assert rig.hal.pump_running("PUMP_A") and not rig.hal.pump_running("PUMP_B")


def test_commands_follow_the_gateways_command_schema():
    dose = dose_command(dose_id=dose_id_for("x/A"), target="zone:1", relay="relay1",
                        seconds=12.3456, issued_at=1793952000)
    assert dose["args"]["seconds"] == 12.346 and len(dose["id"]) <= 47
    assert len(dose["origin"]) <= 15  # sf/cmd/1 maxLength
    off = switch_off_command(command_id="c1", target="zone:2", relays=["relay3"],
                             issued_at=1793952000)
    assert off["args"] == {"relays": {"relay3": False}}
    assert dose_id_for("x/A") == dose_id_for("x/A") != dose_id_for("x/B")


def test_topics_and_the_senml_status():
    assert subtopic("m/w/c/t/sf/zone/1", "m/w/c/t") == "sf/zone/1"
    assert subtopic("m/w/c/other/sf/zone/1", "m/w/c/t") is None
    assert status_of([{"bn": "gateway-01:", "n": "status", "vs": "offline"}]) == (
        "gateway-01", False)
    assert status_of({"vs": "online"}) is None


def test_the_rig_zone_comes_from_the_config_pack(rig_pack):
    [zone] = load_rig_zones(rig_pack)
    assert (zone.device_id, zone.zone_id, zone.target, zone.gateway) == (
        "ZC1", "Z1", "zone:1", "gateway-01")
    assert zone.pump_ml_per_s == {"A": 2.0, "B": 2.0, "ACID": 2.0}  # hardware.json
    assert zone.limits["maxPerDoseMl"]["A"] == 300  # limits.json


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rigZone": 3}, "rigZone"),
        ({"pumps": {"A": "relay1", "B": "relay2"}}, "pumps"),
        ({"pumps": {"A": "relay1", "B": "relay1", "ACID": "relay3"}}, "own relay"),
        ({"pumps": {"A": "relay4", "B": "relay2", "ACID": "relay3"}}, "own relay"),
        ({"tdsPpmPerMsCm": 0}, "tdsPpmPerMsCm"),
        ({"dayTankLevelSensors": "no"}, "dayTankLevelSensors"),
        ({"gateway": ""}, "gateway"),
    ],
)
def test_a_bad_rig_entry_is_refused_with_its_key(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        load_rig_zones(make_rig_pack(tmp_path, **changes))


def slow_pump_pack(tmp_path, flow_ml_per_s):
    pack = make_rig_pack(tmp_path)
    hardware = json.loads((pack / "hardware.json").read_text(encoding="utf-8"))
    hardware["pumps"]["doseLines"]["A"]["flowMlPerS"] = flow_ml_per_s
    (pack / "hardware.json").write_text(json.dumps(hardware), encoding="utf-8")
    return pack


def test_a_line_whose_largest_dose_needs_over_600_s_is_refused(tmp_path):
    """Else a dose the review approved would be one the rig can't run (sf/cmd/1: 0.1-600 s)."""
    with pytest.raises(ValueError, match=r"line A's largest dose, 300 mL at 0\.4 mL/s, is 750 s"):
        load_rig_zones(slow_pump_pack(tmp_path / "slow", 0.4))
    [zone] = load_rig_zones(slow_pump_pack(tmp_path / "just", 0.5))  # 300 mL: 600 s exactly
    assert zone.pump_ml_per_s["A"] == 0.5


def test_client_a_runs_nothing_on_the_rig():
    from sim.settings import load_settings

    assert load_rig_zones(load_settings({}).client_dir) == []
    assert RIG_ENTRY["link"] == "SF_GATEWAY"


def test_topic_bases_and_offline_time_come_from_the_environment():
    rig = load_rig_settings({"RIG_TELEMETRY_TOPIC": "m/ws/c/tel/",
                             "RIG_COMMAND_TOPIC": "m/ws/c/cmd", "RIG_OFFLINE_S": "45"})
    assert (rig.telemetry_topic, rig.command_topic, rig.offline_after_s) == (
        "m/ws/c/tel", "m/ws/c/cmd", 45.0)
    with pytest.raises(ValueError, match="RIG_OFFLINE_S"):
        load_rig_settings({"RIG_OFFLINE_S": "0"})


def test_validate_config_checks_a_rig_entry(tmp_path):
    """scripts/validate_config.py: the gate T1-31's client_B pack goes through."""
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).parents[3] / "scripts" / "validate_config.py"
    good = make_rig_pack(tmp_path / "good").parent
    bad = make_rig_pack(tmp_path / "bad", rigZone=3, pumps={"A": "relay1"}).parent

    def validate(clients):
        return subprocess.run([sys.executable, str(script), str(clients)], capture_output=True,
                              text=True, check=False)

    slow = slow_pump_pack(tmp_path / "slow", 0.4).parent

    assert validate(good).returncode == 0
    result = validate(bad)
    assert result.returncode == 1
    said = result.stdout + result.stderr
    assert "controllers.ZC1.rigZone must be 1 or 2" in said
    assert "controllers.ZC1.pumps must map each dose line" in said
    result = validate(slow)
    assert result.returncode == 1
    assert "line A's largest dose, 300 mL at 0.4 mL/s, is 750 s" in result.stdout + result.stderr
