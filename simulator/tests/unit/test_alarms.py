"""Alarm limits come from the config pack; crossing one sends one reading, not a flood (T4-5)."""

from __future__ import annotations

import pytest

from sim.alarms import Alarm, AlarmWatch, climate_alarms, zone_alarms
from sim.config import read_pack_file
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SOL_TEMP = Alarm("solTempC", 26.0, above=True)
FLOW = Alarm("circFlowLpm", 15.0, above=False)


def test_client_a_zone_limits_come_from_alerts_json():
    alerts = read_pack_file(CLIENT_A, "alerts.json")
    assert set(zone_alarms(alerts, "Z1", ["A", "B", "ACID"])) == {
        Alarm("solTempC", 26.0, above=True),
        Alarm("circFlowLpm", 15.0, above=False),
        Alarm("reservoirPct", 40, above=False),
        Alarm("dayTankPct.A", 20, above=False),
        Alarm("dayTankPct.B", 20, above=False),
        Alarm("dayTankPct.ACID", 20, above=False),
    }


def test_changing_the_config_changes_the_limit():
    alerts = {"zones": {"Z9": {"alarms": {"solTempMaxC": 31.5}}}}
    assert zone_alarms(alerts, "Z9", []) == (Alarm("solTempC", 31.5, above=True),)


def test_day_tank_alarms_follow_the_zones_lines_not_a_fixed_list():
    alerts = {"events": {"DAYTANK_LOW": {"thresholdPct": 10}}}
    assert zone_alarms(alerts, "Z1", ["BASE"]) == (Alarm("dayTankPct.BASE", 10, above=False),)


def test_a_limit_that_is_not_configured_raises_no_alarm():
    assert zone_alarms({}, "Z1", ["A"]) == ()
    assert climate_alarms({}) == ()


def test_a_limit_that_is_not_a_number_is_a_clear_error():
    with pytest.raises(ValueError, match="solTempC"):
        zone_alarms({"zones": {"Z1": {"alarms": {"solTempMaxC": "hot"}}}}, "Z1", [])


def test_climate_limits_are_the_air_setpoints():
    assert climate_alarms({"airTempMaxC": 30.0, "airRhMax": 85, "solTempMaxC": 24.0}) == (
        Alarm("airTempC", 30.0, above=True),
        Alarm("airRh", 85, above=True),
    )


@pytest.mark.parametrize(
    ("alarm", "value", "breached"),
    [
        (SOL_TEMP, 26.1, True),
        (SOL_TEMP, 26.0, False),  # on the limit has not crossed it
        (SOL_TEMP, None, False),  # no measurement: its quality reports that
        (FLOW, 14.9, True),
        (FLOW, 15.0, False),
        (FLOW, 0.0, True),  # a real zero flow is very much an alarm
        (FLOW, None, False),
    ],
)
def test_breached_by(alarm, value, breached):
    assert alarm.breached_by(value) is breached


def reading(temp=20.0, flow=30.0):
    return {"solTempC": temp, "circFlowLpm": flow}


def watch():
    return AlarmWatch([SOL_TEMP, FLOW])


def test_no_crossing_means_nothing_is_urgent():
    alarms = watch()
    alarms.reported(reading(), periodic=True)
    assert alarms.crossed(reading(temp=25.9)) is False


def test_crossing_a_limit_is_urgent_once_not_for_as_long_as_it_stays_beyond_it():
    alarms = watch()
    alarms.reported(reading(), periodic=True)
    assert alarms.crossed(reading(temp=26.5)) is True
    alarms.reported(reading(temp=26.5), periodic=False)
    for temp in (26.6, 27.0, 40.0):
        assert alarms.crossed(reading(temp=temp)) is False


def test_a_value_already_beyond_the_limit_at_the_first_reading_is_not_a_crossing():
    alarms = watch()
    alarms.reported(reading(temp=30.0), periodic=True)
    assert alarms.crossed(reading(temp=30.0)) is False


def test_coming_back_is_a_crossing_too():
    alarms = watch()
    alarms.reported(reading(temp=30.0), periodic=True)
    assert alarms.crossed(reading(temp=25.0)) is True


def test_a_value_flapping_across_a_limit_gets_one_urgent_reading_per_period():
    alarms = watch()
    alarms.reported(reading(), periodic=True)
    urgent = 0
    for temp in (27.0, 25.0, 27.0, 25.0, 27.0, 25.0):
        if alarms.crossed(reading(temp=temp)):
            urgent += 1
            alarms.reported(reading(temp=temp), periodic=False)
    assert urgent == 1
    alarms.reported(reading(temp=25.0), periodic=True)  # the next period's reading reports the end
    assert alarms.crossed(reading(temp=27.0)) is True  # and a new period may fire again


def test_a_different_value_crossing_is_not_held_back_by_the_first():
    alarms = watch()
    alarms.reported(reading(), periodic=True)
    alarms.reported(reading(temp=27.0), periodic=False)
    assert alarms.crossed(reading(temp=27.0, flow=10.0)) is True


def test_a_periodic_reading_that_already_shows_the_crossing_needs_no_second_message():
    alarms = watch()
    alarms.reported(reading(), periodic=True)
    alarms.reported(reading(temp=27.0), periodic=True)
    assert alarms.crossed(reading(temp=27.0)) is False


def test_map_readings_are_watched_per_line():
    alarms = AlarmWatch([Alarm("dayTankPct.A", 20, above=False)])
    alarms.reported({"dayTankPct": {"A": 64, "B": 66}}, periodic=True)
    assert alarms.crossed({"dayTankPct": {"A": 64, "B": 5}}) is False  # B has no limit here
    assert alarms.crossed({"dayTankPct": {"A": 19, "B": 66}}) is True
