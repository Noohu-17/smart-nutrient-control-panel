"""A device the clock task takes readings from (contract 6.1-6.3).

take_reading() is the periodic message; take_urgent_reading() sends one out of turn when
something can't wait for the next period. Which message, and what is urgent, is up to each
kind of device. The clock task never talks to MQTT: readings wait in the device's outbox.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, ClassVar

from ..alarms import Alarm, AlarmWatch
from .base import SimDevice


class ReadingDevice(SimDevice):
    message_type: ClassVar[str]
    qos: ClassVar[int] = 0  # contract 4: telemetry is QoS 0, not retained
    retain: ClassVar[bool] = False

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        reading_topic: str,
        alarms: Iterable[Alarm] = (),
        **options: Any,
    ):
        super().__init__(device_id, kind, site, boot_id, **options)
        self.reading_topic = reading_topic
        self.alarm_watch = AlarmWatch(alarms)

    def body(self) -> dict[str, Any]:
        """The reading as it stands now, without its envelope."""
        raise NotImplementedError

    def urgent(self, body: dict[str, Any]) -> bool:
        return self.alarm_watch.crossed(body["readings"])

    def note_sent(self, body: dict[str, Any], *, periodic: bool) -> None:
        self.alarm_watch.reported(body["readings"], periodic=periodic)

    def take_reading(self, sim_ts: datetime | None) -> None:
        self._queue(self.body(), sim_ts, periodic=True)

    def take_urgent_reading(self, sim_ts: datetime | None) -> None:
        body = self.body()
        if self.urgent(body):
            self._queue(body, sim_ts, periodic=False)

    def _queue(self, body: dict[str, Any], sim_ts: datetime | None, *, periodic: bool) -> None:
        self.note_sent(body, periodic=periodic)
        ts, sim_ts = self._now(), sim_ts if self.clock else None  # simTs: playground sites only

        def build() -> dict[str, Any]:
            return {**self.envelope.next(self.message_type, ts, sim_ts), **body}

        self.send(self.reading_topic, build, qos=self.qos, retain=self.retain)
