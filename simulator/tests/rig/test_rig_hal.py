"""The rig's HAL (T1-32): readings in contract units and quality, equipment, and the config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rig_support import RIG_ENTRY, Rig, make_rig_pack, zone_doc

from sim.rig.config import load_rig_settings, load_rig_zones, smallest_dose_ml
from sim.rig.hal import RigHal
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


def test_a_simulated_zone_is_never_run_on_the_rig(tmp_path):
    """Contract 2.4 (T1-33): a controller with link SF_GATEWAY runs a LIVE zone."""
    with pytest.raises(ValueError, match="source must be LIVE"):
        load_rig_zones(make_rig_pack(tmp_path, zone_source="SIMULATED"))


CLIENT_B = Path(__file__).parents[3] / "config" / "clients" / "client_B"


def test_client_b_runs_both_zones_on_the_rig_at_their_own_pump_rates():
    """T1-31: Zone A is the rig's Zone 1, the actuator box, its pumps calibrated at 15 mL/min;
    Zone B is Zone 2, the gateway's own relays, whose pumps run 1 L/min (hardware.json
    pumps.zones.Z2). The simulator never plays a live farm's zones: it refuses the pack."""
    from sim.farm import load_farm

    zone_a, zone_b = load_rig_zones(CLIENT_B)
    assert (zone_a.device_id, zone_a.zone_id, zone_a.target) == ("ZC1", "Z1", "zone:1")
    assert (zone_b.device_id, zone_b.zone_id, zone_b.target) == ("ZC2", "Z2", "zone:2")
    assert zone_a.pumps == zone_b.pumps == {"N": "relay1", "P": "relay2", "K": "relay3"}
    assert zone_a.pump_ml_per_s == {"N": 0.25, "P": 0.25, "K": 0.25}
    assert zone_b.pump_ml_per_s == {"N": 16.667, "P": 16.667, "K": 16.667}
    assert not zone_a.pumps_on_gateway and zone_b.pumps_on_gateway
    with pytest.raises(ValueError, match=r"simulator\.json: model"):
        load_farm(CLIENT_B)


def client_b_hal(tmp_path, zone_id):
    zone = next(z for z in load_rig_zones(CLIENT_B) if z.zone_id == zone_id)
    sent = []
    return RigHal(zone, tmp_path / f"nvs_{zone.device_id}.json", sent.append), sent


def test_each_client_b_zone_times_its_doses_at_its_own_pump_rate(tmp_path):
    """2 mL, Zone B's largest dose, is 0.12 s of the gateway's relay; on Zone A it is 8 s."""
    hal_a, sent_a = client_b_hal(tmp_path, "Z1")
    hal_b, sent_b = client_b_hal(tmp_path, "Z2")
    hal_a.run_pump_for("PUMP_N", 2, "dose/N")
    hal_b.run_pump_for("PUMP_N", 2, "dose/N")
    assert [(c["target"], c["args"]["relay"], c["args"]["seconds"]) for c in sent_a] == [
        ("zone:1", "relay1", 8.0)]
    assert [(c["target"], c["args"]["relay"], c["args"]["seconds"]) for c in sent_b] == [
        ("zone:2", "relay1", 0.12)]


def test_a_client_b_zone_b_dose_of_1_ml_is_too_short_for_the_rig(tmp_path, caplog):
    """1 mL at 16.667 mL/s is 0.06 s, under the rig's 0.1 s: it is logged when the zone is
    loaded, and the rig is never sent it (the line delivers nothing)."""
    caplog.set_level("WARNING", logger="sim.rig")
    hal_b, sent_b = client_b_hal(tmp_path, "Z2")
    assert "Z2: line N's pump runs 16.667 mL/s, so a dose under 2 mL" in caplog.text
    assert "Z1:" not in caplog.text  # 1 mL at 0.25 mL/s is 4 s
    hal_b.run_pump_for("PUMP_K", 1, "small/K")
    assert sent_b == [] and not hal_b.pump_running("PUMP_K")


@pytest.mark.parametrize(
    ("ml_per_s", "smallest"), [(0.25, 1), (10, 1), (10.5, 2), (16.667, 2), (30, 3)]
)
def test_the_smallest_dose_a_pump_can_run_on_the_rig(ml_per_s, smallest):
    assert smallest_dose_ml(ml_per_s) == smallest


def zone_rate_pack(tmp_path, rates):
    """make_rig_pack with Z1's own pump rates, hardware.json pumps.zones.Z1.doseLines."""
    pack = make_rig_pack(tmp_path)
    hardware = json.loads((pack / "hardware.json").read_text(encoding="utf-8"))
    hardware["pumps"]["zones"] = {
        "Z1": {"doseLines": {line: {"flowMlPerS": flow} for line, flow in rates.items()}}
    }
    (pack / "hardware.json").write_text(json.dumps(hardware), encoding="utf-8")
    return pack


def test_a_zones_own_pump_rate_stands_over_the_shared_one(tmp_path):
    """Lines the zone gives no rate of keep pumps.doseLines' (client_A: 2 mL/s)."""
    [zone] = load_rig_zones(zone_rate_pack(tmp_path, {"A": 5.0}))
    assert zone.pump_ml_per_s == {"A": 5.0, "B": 2.0, "ACID": 2.0}


@pytest.mark.parametrize(
    ("rates", "message"),
    [
        ({"A": 0}, r"pumps\.zones\.Z1\.doseLines\.A\.flowMlPerS must be a number > 0"),
        ({"B": "fast"}, r"pumps\.zones\.Z1\.doseLines\.B\.flowMlPerS"),
        # the largest dose is checked at the zone's own rate
        ({"ACID": 0.1}, r"line ACID's largest dose, 100 mL at 0\.1 mL/s, is 1000 s"),
    ],
)
def test_a_zones_own_pump_rate_is_checked_as_the_shared_one_is(tmp_path, rates, message):
    with pytest.raises(ValueError, match=message):
        load_rig_zones(zone_rate_pack(tmp_path, rates))


def test_a_dose_too_short_for_the_rig_ends_without_pumping(tmp_path, clock):
    """The controller's answer to a line the rig can't run: nothing was sent, nothing delivered."""
    rig = Rig(tmp_path, zone_rate_pack(tmp_path, {"A": 16.667}), clock)
    cmd_id = rig.dose(A=1)
    for _ in range(4):
        rig.step()
    assert rig.doses_sent() == []
    assert rig.states(cmd_id)[-1]["state"] == "FAILED"


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

    simulated = make_rig_pack(tmp_path / "simulated", zone_source="SIMULATED").parent
    unknown = make_rig_pack(tmp_path / "unknown", zone_source="REAL").parent
    result = validate(simulated)
    assert result.returncode == 1
    assert "zone Z1 runs on the rig, so its layout.json source must be LIVE" in result.stderr
    result = validate(unknown)
    assert result.returncode == 1
    assert "zone Z1: source must be one of ['LIVE', 'SIMULATED'], got 'REAL'" in result.stderr
