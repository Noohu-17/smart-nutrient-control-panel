"""The simulated clock: speeds 0, 1, 60, 288 (T4-3).

A fake real-time source makes "one real minute" take no real time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from sim.clock import SimClock
from sim.config import load_speed_options
from sim.settings import load_settings

START = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
SPEEDS = (0, 1, 60, 288)


class FakeRealTime:
    def __init__(self):
        self.s = 1000.0

    def __call__(self) -> float:
        return self.s


def make_clock(speed):
    real = FakeRealTime()
    return SimClock(START, speed, SPEEDS, monotonic=real), real


def test_at_x60_one_real_minute_is_one_simulated_hour():
    clock, real = make_clock(60)
    real.s += 60
    assert clock.now() - START == timedelta(hours=1)


def test_at_x288_one_day_passes_in_five_real_minutes():
    clock, real = make_clock(288)
    real.s += 5 * 60
    assert clock.now() - START == timedelta(days=1)


def test_at_x1_simulated_time_follows_real_time():
    clock, real = make_clock(1)
    real.s += 42.5
    assert clock.now() - START == timedelta(seconds=42.5)


def test_speed_0_pauses():
    clock, real = make_clock(0)
    real.s += 3600
    assert clock.now() == START


def test_changing_speed_carries_on_without_a_jump():
    clock, real = make_clock(60)
    real.s += 60  # 1 h at x60
    clock.set_speed(0)
    real.s += 600  # paused: nothing
    clock.set_speed(288)
    real.s += 300  # 1 day at x288
    assert clock.now() - START == timedelta(days=1, hours=1)
    assert clock.speed == 288


def test_tick_gives_simulated_seconds_since_the_last_tick():
    clock, real = make_clock(60)
    real.s += 1
    assert clock.tick() == pytest.approx(60.0)
    real.s += 0.5
    assert clock.tick() == pytest.approx(30.0)
    assert clock.tick() == 0.0


@pytest.mark.parametrize("speed", [2, -1, True, 1000])
def test_only_configured_speeds_are_allowed(speed):
    with pytest.raises(ValueError, match="speed must be one of"):
        SimClock(START, speed, SPEEDS)
    clock, _ = make_clock(1)
    with pytest.raises(ValueError, match="speed must be one of"):
        clock.set_speed(speed)


def test_start_must_be_timezone_aware_and_speeds_configured():
    with pytest.raises(ValueError, match="timezone"):
        SimClock(datetime(2026, 11, 9, 6, 0), 1, SPEEDS)
    with pytest.raises(ValueError, match="speedOptions"):
        SimClock(START, 1, ())


def test_speeds_come_from_simulator_json():
    assert load_speed_options(load_settings({}).client_dir) == SPEEDS
