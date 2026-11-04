"""A device the clock task takes readings from (contract 6.1-6.3).

take_reading() is the periodic message; take_urgent_reading() sends one out of turn when
something can't wait for the next period. Which message, and what is urgent, is up to each
kind of device. The clock task never talks to MQTT: readings wait in the device's outbox.

StateDevice adds a controller's retained state (contract 6.8, 6.9): sent at the start, on every
change, and again when the link comes back. What counts as a change is the message body, which
has no timestamp or ID in it, so comparing two bodies compares only what the farm can see.
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

    def current_sim_ts(self) -> datetime | None:
        return self.clock.now() if self.clock else None

    def take_reading(self, sim_ts: datetime | None) -> None:
        self._queue(self.body(), sim_ts, periodic=True)

    def take_urgent_reading(self, sim_ts: datetime | None) -> None:
        body = self.body()
        if self.urgent(body):
            self._queue(body, sim_ts, periodic=False)

    def _queue(self, body: dict[str, Any], sim_ts: datetime | None, *, periodic: bool) -> None:
        self.note_sent(body, periodic=periodic)
        self._send_message(
            self.reading_topic, self.message_type, body, sim_ts, qos=self.qos, retain=self.retain
        )

    def _send_message(
        self,
        topic: str,
        msg_type: str,
        body: dict[str, Any],
        sim_ts: datetime | None,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        ts, sim_ts = self._now(), sim_ts if self.clock else None  # simTs: playground sites only

        def build() -> dict[str, Any]:
            return {**self.envelope.next(msg_type, ts, sim_ts), **body}

        self.send(topic, build, qos=qos, retain=retain)


class StateDevice(ReadingDevice):
    """A controller: readings, plus its state at QoS 1, retained (contract 4)."""

    state_type: ClassVar[str]
    state_with_readings: ClassVar[bool] = False

    def __init__(
        self,
        device_id: str,
        kind: str,
        site: str,
        boot_id: int,
        *,
        state_topic: str,
        **options: Any,
    ):
        super().__init__(device_id, kind, site, boot_id, **options)
        self.state_topic = state_topic
        self._last_state: dict[str, Any] | None = None

    def state_body(self) -> dict[str, Any]:
        """The state as it stands now, without its envelope."""
        raise NotImplementedError

    def publish_state(self, sim_ts: datetime | None) -> None:
        self._last_state = self.state_body()
        self._send_message(
            self.state_topic, self.state_type, self._last_state, sim_ts, qos=1, retain=True
        )

    def publish_state_if_changed(self, sim_ts: datetime | None) -> None:
        """Send the state if it changed. Controllers call this after each transition."""
        if self.state_body() != self._last_state:
            self.publish_state(sim_ts)

    def take_urgent_reading(self, sim_ts: datetime | None) -> None:
        body = self.body()
        if self.urgent(body):
            self._queue(body, sim_ts, periodic=False)
        else:
            self.publish_state_if_changed(sim_ts)

    def on_reconnect(self) -> None:
        self.publish_state(self.current_sim_ts())

    def _queue(self, body: dict[str, Any], sim_ts: datetime | None, *, periodic: bool) -> None:
        # The state goes first: it is what the screens react to (contract 6.8).
        if self.state_with_readings:
            self.publish_state(sim_ts)
        else:
            self.publish_state_if_changed(sim_ts)
        super()._queue(body, sim_ts, periodic=periodic)
