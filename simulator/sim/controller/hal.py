"""The hardware abstraction layer: all a controller may touch (contract 9).

A real ESP32 will provide the same calls. Names are provisional until io_map.json is agreed
(contract 2.5): docs/controller-spec.md lists them. Chiller setpoints join when the climate
controller needs them.
"""

from __future__ import annotations

from typing import Protocol

Reading = tuple[float | None, str]  # the value, and its quality from contract 6.1


class HAL(Protocol):
    def read_sensor(self, sensor_id: str) -> Reading: ...

    def read_input(self, input_id: str) -> bool: ...

    def set_output(self, output_id: str, on: bool) -> None: ...

    def take_flow_ml(self, meter_id: str) -> float:
        """mL a line's flow meter (FLOW_<line>) counted since the last call.

        Real firmware counts pulses and converts them with the meter's calibration.
        """
        ...

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


class TimedPumps(Protocol):
    """A HAL whose dosing pumps run by time, with no flow meter: the prototype rig (T1-32).

    It sets `timed_pumps = True`. The controller then asks for a line's mL at once instead of
    switching its pump on, and the hardware runs it for mL / its pump rate and stops by itself.
    take_flow_ml() counts what a pump delivered once the hardware reports how long it ran, and
    set_output(pump, False) stops a pump early.
    """

    timed_pumps: bool

    def run_pump_for(self, output_id: str, ml: float, run_id: str) -> None:
        """Run PUMP_<line> for `ml`. run_id names this run (cmdId/line): never run it twice."""
        ...

    def pump_running(self, output_id: str) -> bool:
        """Whether the pump is still running, or asked to and not yet reported back."""
        ...
