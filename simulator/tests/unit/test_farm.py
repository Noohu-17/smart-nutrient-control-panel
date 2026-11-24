"""The simulated farm: built from the config pack, moved only by physics (T4-5)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from sim.farm import load_farm
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
# 2026-11-09 18:30 UTC is midnight in Asia/Kolkata (layout.json).
NOON_IST = datetime(2026, 11, 9, 6, 30, tzinfo=UTC)
MIDNIGHT_IST = datetime(2026, 11, 9, 18, 30, tzinfo=UTC)


def edited_pack(tmp_path, file_name, edit):
    client_dir = shutil.copytree(CLIENT_A, tmp_path / "client")
    path = client_dir / file_name
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    return client_dir


@pytest.fixture
def farm():
    return load_farm(CLIENT_A)


def test_every_layout_zone_starts_in_the_demo_starting_state(farm):
    """simulator.json demoStart over model.initialState (T4-9, C-3): Zone A 0.2 below target."""
    assert list(farm.zones) == ["Z1", "Z2"]
    assert farm.zones["Z1"].water().ec_ms_cm == pytest.approx(1.30)
    water = farm.zones["Z2"].water()
    assert water.volume_l == pytest.approx(936.0)
    assert water.ec_ms_cm == pytest.approx(1.20)
    assert (water.ph, water.temp_c) == (6.04, 23.4)
    assert farm.zones["Z2"].day_tank_l == {"A": 32.0, "B": 33.0, "ACID": 40.5}


def test_without_a_demo_start_every_zone_starts_as_the_initial_state(tmp_path):
    farm = load_farm(edited_pack(tmp_path, "simulator.json", lambda d: d.pop("demoStart")))
    assert {z: farm.zones[z].water().ec_ms_cm for z in farm.zones} == pytest.approx(
        {"Z1": 1.62, "Z2": 1.62}
    )


def test_a_demo_start_can_set_a_day_tank_and_keeps_the_others(tmp_path):
    def low_acid(simulator):
        simulator["demoStart"]["zones"]["Z1"]["dayTankPct"] = {"ACID": 10}

    farm = load_farm(edited_pack(tmp_path, "simulator.json", low_acid))
    assert farm.zones["Z1"].day_tank_l == {"A": 32.0, "B": 33.0, "ACID": 5.0}


def test_zones_do_not_share_state(farm):
    farm.zones["Z1"].day_tank_l["A"] = 1.0
    farm.zones["Z1"].outputs["Z1-CIRC"] = "OFF"
    assert farm.zones["Z2"].day_tank_l["A"] == 32.0
    assert farm.zones["Z2"].outputs["Z2-CIRC"] == "ON"


def test_only_circulation_runs_at_the_start(farm):
    assert farm.zones["Z1"].outputs == {
        "Z1-CIRC": "ON", "Z1-REFILL": "CLOSED",
        "Z1-DOSE-A": "OFF", "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF",
    }


def test_physics_moves_the_water_toward_the_air_and_raw_water(farm):
    before = farm.zones["Z1"].water()
    farm.step(3600.0, NOON_IST)
    after = farm.zones["Z1"].water()
    assert before.temp_c < after.temp_c < farm.climate.air.temp_c  # warms toward the air
    assert before.ph < after.ph < 7.2  # drifts toward the raw water's pH
    assert after.volume_l == before.volume_l  # valve closed, nothing planted yet
    assert after.ec_ms_cm == pytest.approx(before.ec_ms_cm)


def test_a_paused_step_moves_nothing(farm):
    before = farm.zones["Z1"].reservoir
    farm.step(0.0, NOON_IST)
    assert farm.zones["Z1"].reservoir == before


def test_an_open_refill_valve_fills_the_tank_and_dilutes(farm):
    zone = farm.zones["Z1"]
    before = zone.water()
    zone.outputs["Z1-REFILL"] = "OPEN"
    farm.step(60.0, NOON_IST)
    after = zone.water()
    assert after.volume_l == pytest.approx(before.volume_l + 30.0)  # 30 L/min in simulator.json
    assert after.ec_ms_cm < before.ec_ms_cm


def dose_for(zone, seconds):
    """Run line A's pump for `seconds`, then stop it (2 mL/s in hardware.json)."""
    zone.outputs["Z1-DOSE-A"] = "ON"
    zone.step(seconds, 30.0)
    zone.outputs["Z1-DOSE-A"] = "OFF"


def test_dosed_stock_mixes_in_over_the_time_constant(farm):
    """simulator.json model.mixing: 600 s. 100 mL of A adds 0.05 to EC once mixed (936 L)."""
    zone = farm.zones["Z1"]
    start = zone.water().ec_ms_cm
    dose_for(zone, 50.0)  # 100 mL left the day-tank at once
    assert zone.day_tank_l["A"] == pytest.approx(32.0 - 0.1)
    early = zone.water().ec_ms_cm - start
    zone.step(600.0, 30.0)  # one time constant
    one_tau = zone.water().ec_ms_cm - start
    zone.step(3000.0, 30.0)
    mixed = zone.water().ec_ms_cm - start
    assert early < 0.01
    assert mixed == pytest.approx(100 * 0.47 / 936, rel=0.01)
    assert one_tau == pytest.approx(mixed * 0.63, rel=0.1)


def test_nothing_mixes_while_circulation_is_off(farm):
    zone = farm.zones["Z1"]
    start = zone.water().ec_ms_cm
    dose_for(zone, 50.0)
    zone.outputs["Z1-CIRC"] = "OFF"
    zone.step(3600.0, 30.0)
    still = zone.water().ec_ms_cm
    zone.outputs["Z1-CIRC"] = "ON"
    zone.step(3600.0, 30.0)
    assert still - start < 0.01 < zone.water().ec_ms_cm - start


def test_a_time_constant_of_0_mixes_at_once(tmp_path):
    def instant(simulator):
        simulator["model"]["mixing"]["timeConstantS"] = 0

    zone = load_farm(edited_pack(tmp_path, "simulator.json", instant)).zones["Z1"]
    start = zone.water().ec_ms_cm
    dose_for(zone, 50.0)
    assert zone.water().ec_ms_cm - start == pytest.approx(100 * 0.47 / 936, rel=0.01)


def test_circulation_flow_follows_the_pump_and_the_config(farm):
    zone = farm.zones["Z1"]
    assert zone.circ_flow_lpm == 31.0
    zone.outputs["Z1-CIRC"] = "OFF"
    assert zone.circ_flow_lpm == 0.0


def test_equipment_state_never_changes_by_itself(farm):
    outputs = {zone_id: dict(zone.outputs) for zone_id, zone in farm.zones.items()}
    equipment = farm.climate.equipment()
    for hour in range(48):
        farm.step(3600.0, NOON_IST + timedelta(hours=hour))
    assert {zone_id: zone.outputs for zone_id, zone in farm.zones.items()} == outputs
    assert farm.climate.equipment() == equipment  # contract 0: every action starts with a person


@pytest.mark.parametrize(("stage", "on"), [(0, 0), (1, 1), (3, 3), (4, 4)])
def test_the_fan_stage_decides_how_many_fans_run(farm, stage, on):
    farm.climate.fan_stage = stage
    equipment = farm.climate.equipment()
    states = [equipment[fan] for fan in farm.climate.fans]
    assert states.count("ON") == on
    assert states == ["ON"] * on + ["OFF"] * (len(states) - on)  # first fan first


def test_equipment_snapshots_cannot_change_the_farm(farm):
    farm.climate.equipment()["CH1"]["state"] = "ON"
    assert farm.climate.chillers["CH1"]["state"] == "OFF"


# --- run-time counters -----------------------------------------------------------------


def test_run_time_counts_only_what_is_running(farm):
    climate = farm.climate
    farm.step(600.0, NOON_IST)
    assert climate.run_time_today_min() == {"FANS": 0, "PAD1": 0}
    climate.fan_stage = 2
    farm.step(600.0, NOON_IST + timedelta(minutes=10))
    assert climate.run_time_today_min() == {"FANS": 10, "PAD1": 0}
    climate.outputs["PAD1"] = "ON"
    farm.step(300.0, NOON_IST + timedelta(minutes=15))
    assert climate.run_time_today_min() == {"FANS": 15, "PAD1": 5}


def test_run_time_shows_only_whole_minutes(farm):
    farm.climate.fan_stage = 1
    farm.step(0.0, NOON_IST)
    farm.step(119.0, NOON_IST + timedelta(seconds=119))
    assert farm.climate.run_time_today_min()["FANS"] == 1


def test_run_time_starts_again_at_midnight_on_the_farms_own_clock(farm):
    farm.climate.fan_stage = 1
    farm.step(600.0, MIDNIGHT_IST - timedelta(seconds=1))
    assert farm.climate.run_time_today_min()["FANS"] == 10
    # This step ends 120 s into the new day: only that part counts for "today".
    farm.step(600.0, MIDNIGHT_IST + timedelta(seconds=120))
    assert farm.climate.run_time_today_min()["FANS"] == 2


# --- config errors say which key is wrong -----------------------------------------------


@pytest.mark.parametrize(
    ("file_name", "breaks", "message"),
    [
        ("simulator.json", lambda d: d["model"]["air"]["tempByPosC"].pop("MIDDLE"),
         "tempByPosC.MIDDLE"),
        ("simulator.json", lambda d: d["model"]["air"].update(rh=140), "model.air.rh"),
        ("simulator.json", lambda d: d["model"]["initialState"]["dayTankPct"].pop("ACID"),
         "dayTankPct.ACID"),
        ("simulator.json", lambda d: d["model"]["initialState"].update(chillerSetpointC=5),
         "chillerSetpointC"),
        ("simulator.json", lambda d: d["model"]["circulation"].update(flowLpm=-1), "flowLpm"),
        ("simulator.json", lambda d: d["model"]["power"]["phasesV"].pop("l2V"), "phasesV.l2V"),
        ("simulator.json", lambda d: d.pop("model"), "model"),
        ("climate.json", lambda d: d["setpoints"].pop("solTempMaxC"), "setpoints.solTempMaxC"),
        ("layout.json", lambda d: d.update(timezone="Mars/Olympus"), "timezone"),
        ("layout.json", lambda d: d.pop("climate"), "climate"),
        ("layout.json", lambda d: d["climate"].update(airSensors=[]), "airSensors"),
        ("layout.json", lambda d: d["zones"][0].pop("id"), "id"),
        ("simulator.json", lambda d: d["model"].update(mixing={"timeConstantS": -1}),
         "mixing.timeConstantS"),
        ("simulator.json", lambda d: d["demoStart"].update(weather="SNOW"), "demoStart.weather"),
        ("simulator.json", lambda d: d["demoStart"].update(speed=5), "demoStart.speed"),
        ("simulator.json", lambda d: d["demoStart"].update(scenario=""), "demoStart.scenario"),
        ("simulator.json", lambda d: d["demoStart"]["zones"]["Z1"].update(ec=1), "zones.Z1"),
        ("simulator.json", lambda d: d["demoStart"]["zones"]["Z1"].update(ecMsCm="low"), "ecMsCm"),
    ],
)
def test_a_bad_pack_is_refused_with_the_key_at_fault(tmp_path, file_name, breaks, message):
    with pytest.raises(ValueError, match=message):
        load_farm(edited_pack(tmp_path, file_name, breaks))


def test_entries_for_equipment_the_layout_does_not_have_are_ignored(tmp_path):
    def extra(simulator):
        simulator["model"]["air"]["tempByPosC"]["ROOF"] = 50.0
        simulator["model"]["initialState"]["dayTankPct"]["BASE"] = 10
        simulator["demoStart"]["zones"]["Z9"] = {"ecMsCm": 2.0}

    farm = load_farm(edited_pack(tmp_path, "simulator.json", extra))
    assert set(farm.climate.air.temp_by_pos_c) == {"PAD_END", "MIDDLE", "FAN_END"}
    assert "BASE" not in farm.zones["Z1"].day_tank_l
    assert list(farm.zones) == ["Z1", "Z2"]


# --- Live zones and the air's daily cycle (DM-2) ------------------------------------------

CLIENT_B = CLIENT_A.parent / "client_B"
# 2026-11-24 08:30 UTC is 14:00 in Asia/Kolkata; 20:30 UTC is 02:00 the next day.
TWO_PM_IST = datetime(2026, 11, 24, 8, 30, tzinfo=UTC)
TWO_AM_IST = datetime(2026, 11, 24, 20, 30, tzinfo=UTC)


def test_a_live_zone_gets_no_model(tmp_path):
    def z1_live(layout):
        layout["zones"][0]["source"] = "LIVE"
    farm = load_farm(edited_pack(tmp_path, "layout.json", z1_live))
    assert sorted(farm.zones) == ["Z2"]


def test_client_b_is_only_its_climate_and_power():
    farm = load_farm(CLIENT_B)
    assert farm.zones == {}
    assert sorted(farm.climate.air.temp_by_pos_c) == ["FAN_END", "MIDDLE", "PAD_END"]
    assert farm.climate.air_cycle is not None


def test_the_air_follows_the_daily_cycle():
    """simulator.json: 8 C and 20 % RH swings, warmest at 14:00 local time, around model.air."""
    farm = load_farm(CLIENT_B)
    base = farm.climate.base_air
    farm.climate.step(60, TWO_PM_IST)
    warmest = farm.climate.air
    assert warmest.temp_by_pos_c["MIDDLE"] == pytest.approx(base.temp_by_pos_c["MIDDLE"] + 4)
    assert warmest.rh == pytest.approx(base.rh - 10)
    farm.climate.step(60, TWO_AM_IST)
    coolest = farm.climate.air
    assert coolest.temp_by_pos_c["MIDDLE"] == pytest.approx(base.temp_by_pos_c["MIDDLE"] - 4)
    assert coolest.rh == pytest.approx(base.rh + 10)
    assert coolest.elec_room_temp_c == base.elec_room_temp_c


def test_without_a_daily_cycle_the_air_stays_as_configured():
    farm = load_farm(CLIENT_A)
    before = farm.climate.air
    farm.climate.step(60, NOON_IST)
    assert farm.climate.air_cycle is None
    assert farm.climate.air == before


@pytest.mark.parametrize("cycle, message", [
    ({"tempSwingC": -1, "rhSwingPct": 10, "warmestHour": 14}, ">= 0"),
    ({"tempSwingC": 8, "rhSwingPct": 10, "warmestHour": 24}, "warmestHour"),
    ({"tempSwingC": 8, "rhSwingPct": 10}, "warmestHour"),
    ("warm", "must be an object"),
])
def test_a_daily_cycle_that_makes_no_sense_is_refused(tmp_path, cycle, message):
    def add_cycle(sim):
        sim["model"]["air"]["dailyCycle"] = cycle
    with pytest.raises(ValueError, match=message):
        load_farm(edited_pack(tmp_path, "simulator.json", add_cycle))
