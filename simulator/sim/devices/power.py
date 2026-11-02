"""The power monitor (PM1): POWER_STATE, every 60 s and on every change (contract 6.3).

Retained at QoS 1, so a new subscriber sees the power at once. A broker that restarts forgets
retained messages, so the device sends its state again when the link comes back.
"""

from __future__ import annotations

from typing import Any

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
        **options: Any,
    ):
        topic = power_state_topic(site)
        super().__init__(device_id, kind, site, boot_id, reading_topic=topic, **options)
        self.power = power
        self._last_sent: dict[str, Any] | None = None

    def body(self) -> dict[str, Any]:
        return power_state(self.power)

    def urgent(self, body: dict[str, Any]) -> bool:
        return body != self._last_sent

    def note_sent(self, body: dict[str, Any], *, periodic: bool) -> None:
        self._last_sent = body

    def on_reconnect(self) -> None:
        self._queue(self.body(), self.clock.now() if self.clock else None, periodic=False)
