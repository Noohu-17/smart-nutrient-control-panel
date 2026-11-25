"""The power monitor (PM1): POWER_STATE, every 60 s and on every change (contract 6.3).

Those 60 s are real time, whatever the clock's speed and even while it is paused: the mains
doesn't pause with the farm, and POWER_STATE says what is true now, not what a reading saw.

Retained at QoS 1, so a new subscriber sees the power at once. A broker that restarts forgets
retained messages, so the device sends its state again when the link comes back.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..clock import READING_S
from ..farm import PowerFarm
from ..messages import power_state, power_state_topic
from .readings import ReadingDevice


class PowerDevice(ReadingDevice):
    message_type = "POWER_STATE"
    qos = 1
    retain = True

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        power: PowerFarm,
        repeat_s: float = READING_S,
        **options: Any,
    ):
        topic = power_state_topic(site)
        super().__init__(device_id, kind, site, boot_id, reading_topic=topic, **options)
        self.power = power
        self.repeat_s = repeat_s
        self._next_repeat = self._monotonic() + repeat_s
        self._last_sent: dict[str, Any] | None = None

    def body(self) -> dict[str, Any]:
        return power_state(self.power)

    def on_reading_due(self, sim_ts: datetime) -> None:
        """Not on the simulated grid: urgent() repeats the state on real time instead."""

    def urgent(self, body: dict[str, Any]) -> bool:
        return body != self._last_sent or self._monotonic() >= self._next_repeat

    def note_sent(self, body: dict[str, Any], *, periodic: bool) -> None:
        self._last_sent = body
        now = self._monotonic()
        while self._next_repeat <= now:  # a fixed real-time grid: a change doesn't move it
            self._next_repeat += self.repeat_s

    def on_reconnect(self) -> None:
        self._queue(self.body(), self.current_sim_ts(), periodic=False)
