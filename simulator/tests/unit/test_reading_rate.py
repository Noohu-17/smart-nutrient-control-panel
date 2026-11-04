"""The reading rate cap, and POWER_STATE repeating on real time (T4-7).

A fake real-time source makes minutes of real time take no time, and the tests drive
TickLoop.tick() directly, so nothing here sleeps or depends on the machine's speed.
tests/integration/test_reading_rate_broker.py checks both on a real broker.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from app.contract import parse_farm_message

from sim.alarms import zone_alarms
from sim.clock import SimClock, TickLoop, reading_interval_s, tick_interval_s
from sim.config import (
    load_devices,
    load_min_real_reading_interval_s,
    load_site,
    read_pack_file,
)
from sim.devices.power import PowerDevice
from sim.devices.readings import ReadingDevice
from sim.devices.zone import ZoneDevice
from sim.farm import load_farm
from sim.main import make_device
from sim.model.water import ReservoirState
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
REAL_START = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
SPEEDS = (0, 1, 60, 288)
CAP_S = 5.0  # the task's value; test_client_a_sets_the_cap checks it is the shipped one


class FakeRealTime:
    def __init__(self):
        self.s = 1000.0

    def __call__(self) -> float:
        return self.s

    def now(self) -> datetime:
        return REAL_START + timedelta(seconds=self.s)


class FakeFarm:
    def __init__(self):
        self.steps = []

    def step(self, dt_s, now):
        self.steps.append(dt_s)


class Reader:
    def __init__(self):
        self.readings = []

    def on_reading_due(self, sim_ts):
        self.readings.append(sim_ts)

    def take_urgent_reading(self, sim_ts):
        pass


def make_loop(speed, cap_s=CAP_S):
    real, farm, reader = FakeRealTime(), FakeFarm(), Reader()
    clock = SimClock(SIM_START, speed, SPEEDS, monotonic=real)
    return TickLoop(clock, farm, [reader], min_real_reading_s=cap_s), clock, real, farm, reader


def run_for(loop, real, seconds, tick_s=None):
    tick_s = tick_s or tick_interval_s(loop.clock.speed)
    for _ in range(round(seconds / tick_s)):
        real.s += tick_s
        loop.tick()


def sim_after(seconds):
    return SIM_START + timedelta(seconds=seconds)


# --- the interval --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("speed", "interval_s"),
    [
        (0, 60.0),  # paused: nothing falls due anyway
        (1, 60.0),  # real time: one reading a minute, as the contract says
        (12, 60.0),  # exactly where the two rules meet
        (13, 65.0),
        (60, 300.0),
        (288, 1440.0),
    ],
)
def test_the_interval_is_60_simulated_s_or_the_real_cap_whichever_is_longer(speed, interval_s):
    assert reading_interval_s(speed, CAP_S) == interval_s


def test_the_interval_follows_the_configured_cap():
    assert reading_interval_s(288, 1.0) == 288.0
    assert reading_interval_s(288, 0.0) == 60.0  # 0 means no cap


def test_client_a_sets_the_cap():
    assert load_min_real_reading_interval_s(CLIENT_A) == CAP_S


@pytest.mark.parametrize("value", ["5", -1, True, None, [5]])
def test_a_bad_cap_is_a_clear_error(tmp_path, value):
    (tmp_path / "simulator.json").write_text(json.dumps({"minRealReadingIntervalS": value}))
    with pytest.raises(ValueError, match="minRealReadingIntervalS"):
        load_min_real_reading_interval_s(tmp_path)


def test_a_cap_of_zero_is_allowed(tmp_path):
    (tmp_path / "simulator.json").write_text(json.dumps({"minRealReadingIntervalS": 0}))
    assert load_min_real_reading_interval_s(tmp_path) == 0.0


# --- readings at each speed ----------------------------------------------------------


@pytest.mark.parametrize(
    ("speed", "interval_s", "readings_a_minute"),
    [(288, 1440, 12), (60, 300, 12), (1, 60, 1)],
)
def test_readings_a_real_minute_at_each_speed(speed, interval_s, readings_a_minute):
    loop, _, real, _, reader = make_loop(speed)
    loop.tick()
    run_for(loop, real, 60)
    # The one at the start, then the rest exactly on the grid.
    assert reader.readings == [sim_after(interval_s * n) for n in range(readings_a_minute + 1)]


def test_the_grid_does_not_drift_over_a_long_run_at_the_top_speed():
    loop, _, real, _, reader = make_loop(288)
    loop.tick()
    for tick_s in [0.09, 0.13, 0.11, 0.07, 0.2] * 1000:
        real.s += tick_s
        loop.tick()
    assert reader.readings == [sim_after(1440 * n) for n in range(len(reader.readings))]
    assert len(reader.readings) == 121  # 600 real seconds at one per 5 s, plus the first


def test_the_farm_keeps_moving_every_tick_between_capped_readings():
    loop, _, real, farm, reader = make_loop(288)
    loop.tick()
    farm.steps.clear()
    reader.readings.clear()
    ticks = 40
    for _ in range(ticks):  # about 4 real seconds: no reading falls due
        real.s += 0.1
        loop.tick()
    assert reader.readings == []
    assert sum(1 for dt_s in farm.steps if dt_s > 0) == ticks
    assert sum(farm.steps) == pytest.approx(ticks * 0.1 * 288)


def test_a_paused_clock_still_takes_no_readings_under_the_cap():
    loop, _, real, _, reader = make_loop(0)
    loop.tick()
    run_for(loop, real, 600, tick_s=1.0)
    assert reader.readings == [SIM_START]


# --- changing speed ------------------------------------------------------------------


def test_slowing_down_applies_the_new_interval_at_once():
    loop, clock, real, _, reader = make_loop(288)
    loop.tick()
    run_for(loop, real, 2)  # 576 simulated s in: the x288 reading isn't due for 3 real s yet
    assert reader.readings == [SIM_START]
    clock.set_speed(1)
    moment = clock.now()
    loop.tick()
    # Overdue at x1: it can't wait 864 simulated s, 14 real minutes.
    assert reader.readings == [SIM_START, moment]
    run_for(loop, real, 61, tick_s=1.0)
    assert reader.readings[2] == moment + timedelta(seconds=60)


def test_speeding_up_applies_the_cap_at_once():
    loop, clock, real, _, reader = make_loop(1)
    loop.tick()
    run_for(loop, real, 10, tick_s=1.0)
    clock.set_speed(288)
    run_for(loop, real, 4.9, tick_s=0.1)
    assert reader.readings == [SIM_START]  # no 60 s reading squeezes in under the cap
    run_for(loop, real, 0.5, tick_s=0.1)
    assert reader.readings == [SIM_START, sim_after(1440)]


def test_pausing_at_high_speed_sends_no_extra_reading():
    loop, clock, real, _, reader = make_loop(288)
    loop.tick()
    run_for(loop, real, 2)
    clock.set_speed(0)
    run_for(loop, real, 120, tick_s=1.0)
    assert reader.readings == [SIM_START]


def test_resuming_at_real_speed_after_a_pause_reads_at_once_if_overdue():
    loop, clock, real, _, reader = make_loop(288)
    loop.tick()
    run_for(loop, real, 2)
    clock.set_speed(0)
    run_for(loop, real, 30, tick_s=1.0)
    clock.set_speed(1)
    moment = clock.now()
    loop.tick()
    assert reader.readings == [SIM_START, moment]


def test_no_speed_change_ever_moves_the_real_farm_backwards():
    # The model refuses a negative step, so this raises if the loop ever goes back in time.
    real = FakeRealTime()
    farm = load_farm(CLIENT_A)
    clock = SimClock(SIM_START, 288, SPEEDS, monotonic=real)
    loop = TickLoop(clock, farm, [Reader()], min_real_reading_s=CAP_S)
    loop.tick()
    for speed in (1, 288, 0, 60, 1, 0, 288, 60, 288, 1) * 5:
        clock.set_speed(speed)
        for _ in range(7):
            real.s += 0.37
            loop.tick()


# --- alarm readings ------------------------------------------------------------------


def zone_rig(speed=288):
    real = FakeRealTime()
    farm = load_farm(CLIENT_A)
    clock = SimClock(SIM_START, speed, SPEEDS, monotonic=real)
    alarms = zone_alarms(read_pack_file(CLIENT_A, "alerts.json"), "Z1", ["A", "B", "ACID"])
    device = ZoneDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", 1, zone=farm.zones["Z1"],
                        alarms=alarms, clock=clock, monotonic=real, now=real.now)
    loop = TickLoop(clock, farm, [device], min_real_reading_s=CAP_S)
    return loop, device, real, farm


def telemetry_in_outbox(device):
    messages = [item.build() for item in device._outbox]
    device._outbox.clear()
    return [m for m in messages if m["type"] == "ZONE_TELEMETRY"]


def test_an_alarm_reading_still_goes_out_at_once_under_the_cap():
    loop, device, real, farm = zone_rig()
    loop.tick()
    assert len(telemetry_in_outbox(device)) == 1
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 26.5)
    real.s += 0.1  # the next capped reading is almost 5 real seconds away
    loop.tick()
    [urgent] = telemetry_in_outbox(device)
    assert urgent["readings"]["solTempC"] == 26.5
    parse_farm_message(urgent)


def test_an_alarm_reading_does_not_move_the_capped_grid():
    loop, device, real, farm = zone_rig()
    loop.tick()
    telemetry_in_outbox(device)
    zone = farm.zones["Z1"]
    zone.reservoir = ReservoirState(zone.reservoir.volume_l, zone.reservoir.nutrient_g,
                                    zone.reservoir.ph, 26.5)
    real.s += 2.5
    loop.tick()  # the alarm reading, half way to the next one
    real.s += 2.5
    loop.tick()  # and the periodic one, still 5 real seconds after the last periodic one
    stamps = [m["simTs"] for m in telemetry_in_outbox(device)]
    assert stamps == ["2026-07-01T06:12:00.000Z", "2026-07-01T06:24:00.000Z"]


# --- the task's "done when": about 12 messages a real minute at x288 ---------------------


def test_at_x288_each_controller_sends_about_12_readings_a_real_minute():
    real = FakeRealTime()
    farm = load_farm(CLIENT_A)
    clock = SimClock(datetime.now(UTC), 288, SPEEDS, monotonic=real)
    alerts = read_pack_file(CLIENT_A, "alerts.json")
    site = load_site(CLIENT_A)
    devices = [make_device(d, site, 1, farm, alerts, clock) for d in load_devices(CLIENT_A)]
    readers = [d for d in devices if isinstance(d, ReadingDevice)]
    controllers = [d for d in readers if not isinstance(d, PowerDevice)]  # power: tested below
    loop = TickLoop(clock, farm, readers,
                    min_real_reading_s=load_min_real_reading_interval_s(CLIENT_A))
    loop.tick()  # the start
    for device in controllers:
        device._outbox.clear()
    run_for(loop, real, 60)
    readings = {
        device.id: sum(item.topic == device.reading_topic for item in device._outbox)
        for device in controllers
    }
    # 12 periodic each. The zones add one alarm reading: this real minute is 4.8 simulated
    # hours, in which the solution warms past solTempMaxC, and that still goes out at once.
    assert readings == {"ZC1": 13, "ZC2": 13, "CC1": 12}


# --- POWER_STATE on real time --------------------------------------------------------


def power_rig(speed):
    real = FakeRealTime()
    farm = load_farm(CLIENT_A)
    clock = SimClock(SIM_START, speed, SPEEDS, monotonic=real)
    device = PowerDevice("PM1", "POWER_MONITOR", "siet-ph1", 1, power=farm.power, clock=clock,
                         monotonic=real, now=real.now)
    loop = TickLoop(clock, farm, [device], min_real_reading_s=CAP_S)
    loop.tick()  # the start: the first POWER_STATE
    return loop, device, real, farm


def power_in_outbox(device):
    messages = [item.build() for item in device._outbox]
    device._outbox.clear()
    return messages


def seconds_since_start(message):
    ts = datetime.fromisoformat(message["ts"])
    return (ts - (REAL_START + timedelta(seconds=1000))).total_seconds()


def test_power_state_repeats_every_60_real_seconds_while_paused():
    loop, device, real, _ = power_rig(0)
    run_for(loop, real, 185, tick_s=1.0)
    messages = power_in_outbox(device)
    assert [seconds_since_start(m) for m in messages] == [0.0, 60.0, 120.0, 180.0]
    assert {m["simTs"] for m in messages} == {"2026-07-01T06:00:00.000Z"}  # the farm is frozen
    for message in messages:
        parse_farm_message(message)


def test_power_state_stays_qos_1_and_retained_when_it_repeats():
    loop, device, real, _ = power_rig(0)
    device._outbox.clear()
    run_for(loop, real, 61, tick_s=1.0)
    [item] = device._outbox
    assert (item.qos, item.retain) == (1, True)
    assert item.topic == "farm/siet-ph1/power/state"


@pytest.mark.parametrize("speed", [1, 60, 288])
def test_power_state_repeats_on_real_time_at_any_speed(speed):
    loop, device, real, _ = power_rig(speed)
    run_for(loop, real, 121, tick_s=tick_interval_s(speed))
    seconds = [seconds_since_start(m) for m in power_in_outbox(device)]
    assert len(seconds) == 3  # the start, 60 and 120 real seconds; not 12 a minute at x288
    assert seconds[0] == 0.0
    for second, due in zip(seconds[1:], (60.0, 120.0), strict=True):
        assert due <= second <= due + tick_interval_s(speed) + 1e-6


def test_power_state_ignores_the_simulated_reading_grid():
    _, device, _, _ = power_rig(288)
    device._outbox.clear()
    device.on_reading_due(SIM_START)
    assert not device._outbox


def test_a_power_change_goes_out_at_once_and_does_not_move_the_repeat():
    loop, device, real, farm = power_rig(1)
    device._outbox.clear()
    run_for(loop, real, 30, tick_s=1.0)
    farm.power.mains = "LOST"
    run_for(loop, real, 1, tick_s=1.0)
    [changed] = power_in_outbox(device)
    assert changed["mains"] == "LOST"
    assert seconds_since_start(changed) == 31.0
    run_for(loop, real, 29, tick_s=1.0)  # 60 real seconds in: the repeat is still due now
    [repeat] = power_in_outbox(device)
    assert seconds_since_start(repeat) == 60.0


def test_a_power_change_while_paused_goes_out_at_once():
    loop, device, real, farm = power_rig(0)
    device._outbox.clear()
    farm.power.source = "GENERATOR"
    run_for(loop, real, 1, tick_s=1.0)
    assert [m["source"] for m in power_in_outbox(device)] == ["GENERATOR"]


def test_the_repeat_does_not_drift_with_uneven_ticks():
    loop, device, real, _ = power_rig(0)
    device._outbox.clear()
    for tick_s in [0.3, 1.0, 0.7, 0.9, 0.4] * 200:  # 660 real seconds
        real.s += tick_s
        loop.tick()
    seconds = [seconds_since_start(m) for m in power_in_outbox(device)]
    assert len(seconds) == 11
    for n, second in enumerate(seconds, start=1):
        assert 60.0 * n <= second < 60.0 * n + 1.0  # within a tick of its own mark


def test_a_long_stall_sends_one_repeat_not_a_backlog():
    loop, device, real, _ = power_rig(0)
    device._outbox.clear()
    real.s += 3600.0  # a laptop asleep
    loop.tick()
    assert len(power_in_outbox(device)) == 1
    real.s += 59.0
    loop.tick()
    assert power_in_outbox(device) == []  # the grid carries on from where it was
    real.s += 1.0
    loop.tick()
    assert len(power_in_outbox(device)) == 1
