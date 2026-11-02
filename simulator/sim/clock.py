"""Simulated time for the playground (contract 11.4).

The simulated clock can pause (speed 0), run at real speed (1) or fast
(60: one hour per real minute; 288: one day in five minutes). The allowed
speeds come from simulator.json "speedOptions", not from code.

It never changes message time: every message's ts stays real time, so expiry,
staleness and offline detection work as on a real farm. The simulated time
travels in simTs on playground sites (contract 1, 5, 11.4). Heartbeats keep
running on real time whatever the speed.

TickLoop is the clock task, the one scheduler of simulated work: it moves the farm forward
and takes every reading (contract 6.1-6.3, 14). Readings sit on a fixed grid of simulated
time, so they don't drift whatever the speed, the tick length or the real-time jitter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Protocol

log = logging.getLogger("sim.clock")

READING_S = 60.0  # contract 6.1-6.3 and 14: one reading per 60 simulated seconds
REAL_TICK_S = 1.0  # the longest wait between ticks, paused or not
MAX_SIM_PER_TICK_S = 30.0  # fast clocks tick more often: about 10 times a second at x288
MAX_CATCH_UP_READINGS = 60  # after a long stall (a laptop asleep), older readings are skipped


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


def tick_interval_s(speed: float) -> float:
    """Real seconds between ticks: every second, or often enough to limit one tick's step."""
    return min(REAL_TICK_S, MAX_SIM_PER_TICK_S / speed) if speed > 0 else REAL_TICK_S


class SteppedFarm(Protocol):
    def step(self, dt_s: float, now: datetime) -> None: ...


class Reader(Protocol):
    def take_reading(self, sim_ts: datetime) -> None: ...

    def take_urgent_reading(self, sim_ts: datetime) -> None: ...


class TickLoop:
    """Moves the farm forward and asks the devices for their readings.

    A step is split at each reading's due time, so a reading shows the farm exactly as it
    was then, however long the step. Readings out of turn don't move the grid. While the
    clock is paused nothing moves, so no reading falls due and nothing crosses an alarm.
    """

    def __init__(
        self,
        clock: SimClock,
        farm: SteppedFarm,
        readers: Iterable[Reader],
        *,
        reading_s: float = READING_S,
    ):
        self.clock, self.farm, self.readers = clock, farm, tuple(readers)
        self._period = timedelta(seconds=reading_s)
        self._sim_now = clock.last_tick
        self._next_reading = self._sim_now  # the first reading is at the start

    def tick(self) -> None:
        self.clock.tick()
        end = self.clock.last_tick
        self._skip_stale_readings(end)
        while self._next_reading <= end:
            self._move_farm_to(self._next_reading)
            for reader in self.readers:
                reader.take_reading(self._next_reading)
            self._next_reading += self._period
        self._move_farm_to(end)
        for reader in self.readers:
            reader.take_urgent_reading(end)

    async def run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(tick_interval_s(self.clock.speed))

    def _move_farm_to(self, moment: datetime) -> None:
        self.farm.step((moment - self._sim_now).total_seconds(), moment)
        self._sim_now = moment

    def _skip_stale_readings(self, end: datetime) -> None:
        due = (end - self._next_reading) // self._period + 1
        skipped = due - MAX_CATCH_UP_READINGS
        if skipped > 0:
            log.warning("clock jumped ahead: skipping %d old readings", skipped)
            self._next_reading += skipped * self._period
