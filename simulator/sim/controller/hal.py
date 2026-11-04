"""The hardware abstraction layer: all a controller may touch (contract 9).

A real ESP32 will provide the same calls. Names are provisional until io_map.json is agreed
(contract 2.5): docs/controller-spec.md lists them. Pulse counting and chiller setpoints join
when dose execution and the climate controller need them.
"""

from __future__ import annotations

from typing import Protocol

Reading = tuple[float | None, str]  # the value, and its quality from contract 6.1


class HAL(Protocol):
    def read_sensor(self, sensor_id: str) -> Reading: ...

    def read_input(self, input_id: str) -> bool: ...

    def set_output(self, output_id: str, on: bool) -> None: ...

    def now_ms(self) -> int:
        """The controller's own clock; simulated time on playground sites (contract 11.4)."""
        ...

    def utc_ms(self) -> int:
        """Real time since the epoch, the clock a command's expiry is judged on (contract 5)."""
        ...

    def nvs_read(self, key: str) -> dict | None:
        """What survives a reboot."""
        ...

    def nvs_write(self, key: str, value: dict) -> None: ...
