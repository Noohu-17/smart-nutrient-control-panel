"""Simulated time for the playground (contract 11.4).

The simulated clock can pause (speed 0), run at real speed (1) or fast
(60: one hour per real minute; 288: one day in five minutes). The allowed
speeds come from simulator.json "speedOptions", not from code.

It never changes message time: every message's ts stays real time, so expiry,
staleness and offline detection work as on a real farm. The simulated time
travels in simTs on playground sites (contract 1, 5, 11.4). Heartbeats keep
running on real time whatever the speed.

TickLoop is the clock task, the one scheduler of simulated work: it moves the farm forward,
steps the controllers and takes every reading (contract 6.1-6.3, 9, 14). Readings sit on a
fixed grid of simulated time, so they don't drift whatever the tick length or real-time jitter.
A fast clock widens the grid, so that readings never come more often than the config pack allows.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Protocol

log = logging.getLogger("sim.clock")

READING_S = 60.0  # contract 6.1-6.3 and 14: one reading per 60 simulated seconds
REAL_TICK_S = 1.0  # the longest wait between ticks, paused or not
MAX_SIM_PER_TICK_S = 30.0  # fast clocks tick more often: about 10 times a second at x288
MAX_CATCH_UP_READINGS = 60  # after a long stall (a laptop asleep), older readings are skipped
CONTROL_STEP_S = 0.5  # simulated seconds between controller steps: a pump overshoots by one step
MAX_CONTROL_STEPS = 600  # a tick's budget; after a long stall the steps get longer, not more


class SimClock:
    """Simulated time = start + real seconds elapsed x speed, re-anchored at every speed change.

    `monotonic` is the real-time source (seconds); tests pass a fake one, so
    "one real minute" takes no real time.
    """

    def __init__(
        self,
        start: datetime,
        speed: float,
        allowed_speeds: Iterable[float],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware, for example in UTC")
        self.allowed_speeds = tuple(allowed_speeds)
        if not self.allowed_speeds:
            raise ValueError("no clock speeds configured (simulator.json speedOptions)")
        self._monotonic = monotonic
        self._speed = self._checked(speed)
        self._anchor_real = monotonic()
        self._anchor_sim = start
        self._last_tick = start

    @property
    def speed(self) -> float:
        return self._speed

    def set_speed(self, speed: float) -> None:
        """Change speed without a jump: simulated time carries on from where it is."""
        speed = self._checked(speed)
        self._anchor_sim = self.now()
        self._anchor_real = self._monotonic()
        self._speed = speed

    @property
    def last_tick(self) -> datetime:
        """The simulated time tick() last moved to; the start until the first tick."""
        return self._last_tick

    def now(self) -> datetime:
        elapsed_real_s = self._monotonic() - self._anchor_real
        return self._anchor_sim + timedelta(seconds=elapsed_real_s * self._speed)

    def tick(self) -> float:
        """Simulated seconds since the previous tick: the dt for the farm model's step."""
        now = self.now()
        dt_s = (now - self._last_tick).total_seconds()
        self._last_tick = now
        return dt_s

    def _checked(self, speed: float) -> float:
        if isinstance(speed, bool) or speed not in self.allowed_speeds:
            raise ValueError(f"speed must be one of {list(self.allowed_speeds)}, got {speed!r}")
        return speed


def reading_interval_s(speed: float, min_real_s: float, reading_s: float = READING_S) -> float:
    """Simulated seconds between readings: 60, or min_real_s real seconds if that is longer."""
    return max(reading_s, speed * min_real_s)


def tick_interval_s(speed: float) -> float:
    """Real seconds between ticks: every second, or often enough to limit one tick's step."""
    return min(REAL_TICK_S, MAX_SIM_PER_TICK_S / speed) if speed > 0 else REAL_TICK_S


class SteppedFarm(Protocol):
    def step(self, dt_s: float, now: datetime) -> None: ...


class Controller(Protocol):
    def step(self, now_ms: int) -> None: ...


class Reader(Protocol):
    def on_reading_due(self, sim_ts: datetime) -> None: ...

    def take_urgent_reading(self, sim_ts: datetime) -> None: ...


class TickLoop:
    """Moves the farm forward and asks the devices for their readings.

    A step is split at each reading's due time, so a reading shows the farm exactly as it
    was then, however long the step. Readings out of turn don't move the grid. While the
    clock is paused nothing moves, so no reading falls due and nothing crosses an alarm.
    The farm moves on every tick, whatever the interval between readings. With controllers,
    the farm moves in steps of CONTROL_STEP_S simulated seconds, and every controller steps after
    each one, so a pump stops close to its target at any speed. While paused they still step once
    a tick: the farm is frozen, the panel is not.
    """

    def __init__(
        self,
        clock: SimClock,
        farm: SteppedFarm,
        readers: Iterable[Reader],
        *,
        reading_s: float = READING_S,
        min_real_reading_s: float = 0.0,
        controllers: Iterable[Controller] = (),
    ):
        self.clock, self.farm, self.readers = clock, farm, tuple(readers)
        self.controllers = tuple(controllers)
        self._control_step_s = CONTROL_STEP_S
        self._reading_s, self._min_real_reading_s = reading_s, min_real_reading_s
        self._sim_now = clock.last_tick
        self._last_reading: datetime | None = None
        self._next_reading = self._sim_now  # the first reading is at the start

    def tick(self) -> None:
        self.clock.tick()
        end = self.clock.last_tick
        span_s = (end - self._sim_now).total_seconds()
        self._control_step_s = max(CONTROL_STEP_S, span_s / MAX_CONTROL_STEPS)
        period = timedelta(
            seconds=reading_interval_s(self.clock.speed, self._min_real_reading_s, self._reading_s)
        )
        if self._last_reading is not None and self.clock.speed > 0:
            # A new speed's interval applies at once: x288's long gap must not hold up x1. An
            # overdue reading is taken now, never in the past, so the farm only moves forward.
            self._next_reading = max(self._last_reading + period, self._sim_now)
        self._skip_stale_readings(end, period)
        while self._next_reading <= end:
            self._move_farm_to(self._next_reading)
            for reader in self.readers:
                reader.on_reading_due(self._next_reading)
            self._last_reading = self._next_reading
            self._next_reading += period
        self._move_farm_to(end)
        for reader in self.readers:
            reader.take_urgent_reading(end)

    async def run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(tick_interval_s(self.clock.speed))

    def _move_farm_to(self, moment: datetime) -> None:
        start = self._sim_now
        span_s = (moment - start).total_seconds()
        steps = 1
        if self.controllers:
            steps = max(1, math.ceil(span_s / self._control_step_s))
        for n in range(1, steps + 1):
            at = moment if n == steps else start + (moment - start) * n / steps
            self.farm.step((at - self._sim_now).total_seconds(), at)
            self._sim_now = at
            now_ms = round(at.timestamp() * 1000)
            for controller in self.controllers:
                controller.step(now_ms)

    def _skip_stale_readings(self, end: datetime, period: timedelta) -> None:
        due = (end - self._next_reading) // period + 1
        skipped = due - MAX_CATCH_UP_READINGS
        if skipped > 0:
            log.warning("clock jumped ahead: skipping %d old readings", skipped)
            self._next_reading += skipped * period
