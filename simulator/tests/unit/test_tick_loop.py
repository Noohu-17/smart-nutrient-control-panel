"""The clock task: readings on a fixed simulated grid at every speed (T4-5).

A fake real-time source makes hours of real time take no time, and every test drives
TickLoop.tick() directly, so nothing here sleeps or depends on the machine's speed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from sim.clock import (
    MAX_CATCH_UP_READINGS,
    MAX_SIM_PER_TICK_S,
    READING_S,
    SimClock,
    TickLoop,
    tick_interval_s,
)
from sim.farm import load_farm
from sim.messages import zone_telemetry
from sim.settings import load_settings

START = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
SPEEDS = (0, 1, 60, 288)
MINUTE = timedelta(seconds=READING_S)


class FakeRealTime:
    def __init__(self):
        self.s = 1000.0

    def __call__(self) -> float:
        return self.s


class FakeFarm:
    def __init__(self):
        self.steps = []

    def step(self, dt_s, now):
        self.steps.append((dt_s, now))


class Reader:
    """Records when each reading was asked for; `urgent_at` says which ticks have an alarm."""

    def __init__(self, farm=None):
        self.farm = farm
        self.readings = []
        self.urgent_checks = []
        self.farm_steps_seen = []

    def take_reading(self, sim_ts):
        self.readings.append(sim_ts)
        if self.farm:
            self.farm_steps_seen.append(len(self.farm.steps))

    def take_urgent_reading(self, sim_ts):
        self.urgent_checks.append(sim_ts)


def make_loop(speed):
    real, farm, reader = FakeRealTime(), FakeFarm(), Reader()
    clock = SimClock(START, speed, SPEEDS, monotonic=real)
    return TickLoop(clock, farm, [reader]), clock, real, farm, reader


def run_for(loop, real, seconds, tick_s):
    ticks = round(seconds / tick_s)
    for _ in range(ticks):
        real.s += tick_s
        loop.tick()


def grid(count):
    return [START + MINUTE * n for n in range(count)]


def test_the_first_reading_is_at_the_start():
    loop, _, _, _, reader = make_loop(1)
    loop.tick()
    assert reader.readings == [START]


@pytest.mark.parametrize(
    ("speed", "real_s", "readings"),
    [
        (1, 3600, 61),  # one reading a minute, plus the first one at the start
        (60, 60, 61),  # one simulated hour in a real minute
        (288, 300, 1441),  # one simulated day in five real minutes
    ],
)
def test_one_reading_every_60_simulated_seconds_at_every_speed(speed, real_s, readings):
    loop, _, real, _, reader = make_loop(speed)
    loop.tick()
    run_for(loop, real, real_s, tick_interval_s(speed))
    assert reader.readings == grid(readings)


def test_real_time_jitter_never_moves_the_grid():
    loop, _, real, _, reader = make_loop(1)
    loop.tick()
    for tick_s in [0.37, 1.91, 0.02, 3.3, 0.61] * 2000:
        real.s += tick_s
        loop.tick()
    assert reader.readings == grid(len(reader.readings))
    assert len(reader.readings) > 200


def test_a_paused_clock_takes_no_readings_and_does_not_move_the_farm():
    loop, _, real, farm, reader = make_loop(0)
    loop.tick()
    assert reader.readings == [START]  # the farm's state at the start is still reported once
    run_for(loop, real, 3600, 1.0)
    assert reader.readings == [START]
    assert {dt_s for dt_s, _ in farm.steps} == {0.0}


def test_pausing_and_resuming_keeps_the_grid():
    loop, clock, real, _, reader = make_loop(1)
    loop.tick()
    run_for(loop, real, 90, 1.0)  # 90 simulated s: the readings at 0 and 60
    clock.set_speed(0)
    run_for(loop, real, 500, 1.0)
    assert reader.readings == grid(2)
    clock.set_speed(1)
    run_for(loop, real, 30, 1.0)  # the next one falls due 120 simulated s in, not 60 after resume
    assert reader.readings == grid(3)


def test_changing_speed_keeps_the_grid():
    loop, clock, real, _, reader = make_loop(1)
    loop.tick()
    run_for(loop, real, 45, 1.0)
    clock.set_speed(288)
    run_for(loop, real, 10, tick_interval_s(288))
    clock.set_speed(60)
    run_for(loop, real, 7, 0.5)
    assert reader.readings == grid(len(reader.readings))
    assert len(reader.readings) >= 40


def test_a_long_step_is_split_at_each_due_time():
    loop, _, real, farm, reader = make_loop(60)
    loop.tick()
    farm.steps.clear()
    real.s += 4.25  # 255 simulated s in one tick
    loop.tick()
    assert reader.readings == grid(5)  # 0, 60, 120, 180, 240
    assert [dt_s for dt_s, _ in farm.steps] == [60.0, 60.0, 60.0, 60.0, 15.0]
    assert farm.steps[-1][1] == START + timedelta(seconds=255)


def test_each_reading_sees_the_farm_moved_up_to_its_own_due_time():
    real, farm = FakeRealTime(), FakeFarm()
    reader = Reader(farm)
    clock = SimClock(START, 60, SPEEDS, monotonic=real)
    loop = TickLoop(clock, farm, [reader])
    real.s += 3.0  # 180 simulated s
    loop.tick()
    assert reader.farm_steps_seen == [1, 2, 3, 4]  # one more step before each reading


def test_a_real_farm_reads_the_same_however_long_the_ticks_are():
    def reading_after_one_hour(tick_s):
        real = FakeRealTime()
        farm = load_farm(load_settings({}).client_dir)
        taken = {}

        class Sampler:
            def take_reading(self, sim_ts):
                taken[sim_ts] = zone_telemetry(farm.zones["Z1"])["readings"]

            def take_urgent_reading(self, sim_ts):
                pass

        clock = SimClock(START, 60, SPEEDS, monotonic=real)
        loop = TickLoop(clock, farm, [Sampler()])
        run_for(loop, real, 60, tick_s)
        return taken[START + timedelta(hours=1)]

    assert reading_after_one_hour(0.1) == reading_after_one_hour(30.0)


def test_a_stall_beyond_the_catch_up_limit_skips_old_readings_but_keeps_the_grid(caplog):
    loop, _, real, farm, reader = make_loop(288)
    loop.tick()
    reader.readings.clear()
    farm.steps.clear()
    stall_s = (MAX_CATCH_UP_READINGS + 40.5) * READING_S / 288  # a laptop asleep
    real.s += stall_s
    with caplog.at_level(logging.WARNING, logger="sim.clock"):
        loop.tick()
    assert len(reader.readings) == MAX_CATCH_UP_READINGS
    assert reader.readings[-1] == START + MINUTE * (MAX_CATCH_UP_READINGS + 40)
    assert all(b - a == MINUTE for a, b in zip(reader.readings, reader.readings[1:], strict=False))
    assert "skipping" in caplog.text
    # No simulated time is lost, even though readings are.
    assert sum(dt_s for dt_s, _ in farm.steps) == pytest.approx(stall_s * 288)


def test_every_tick_checks_for_urgent_readings_at_its_end():
    loop, _, real, _, reader = make_loop(60)
    loop.tick()
    run_for(loop, real, 2, 0.5)
    assert reader.urgent_checks[-1] == START + timedelta(seconds=120)
    assert len(reader.urgent_checks) == 5  # the first tick and four more


@pytest.mark.parametrize(
    ("speed", "interval_s"),
    [(0, 1.0), (1, 1.0), (30, 1.0), (60, 0.5), (288, MAX_SIM_PER_TICK_S / 288)],
)
def test_tick_interval(speed, interval_s):
    assert tick_interval_s(speed) == pytest.approx(interval_s)


def test_about_ten_ticks_a_second_at_the_fastest_speed():
    assert 9 <= 1 / tick_interval_s(288) <= 10
