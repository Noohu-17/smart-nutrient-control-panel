"""Simulated time for the playground (contract 11.4).

The simulated clock can pause (speed 0), run at real speed (1) or fast
(60: one hour per real minute; 288: one day in five minutes). The allowed
speeds come from simulator.json "speedOptions", not from code.

It never changes message time: every message's ts stays real time, so expiry,
staleness and offline detection work as on a real farm. The simulated time
travels in simTs on playground sites (contract 1, 5, 11.4). Heartbeats keep
running on real time whatever the speed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta


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
