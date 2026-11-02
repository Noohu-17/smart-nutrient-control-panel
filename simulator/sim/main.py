"""Run the farm simulator. From simulator/: python -m sim.main"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from dotenv import load_dotenv

from .alarms import climate_alarms, zone_alarms
from .clock import SimClock, TickLoop
from .config import (
    load_devices,
    load_site,
    load_speed_options,
    playground_enabled,
    read_pack_file,
)
from .devices.base import SimDevice
from .devices.climate import ClimateDevice
from .devices.power import PowerDevice
from .devices.readings import ReadingDevice
from .devices.zone import ZoneDevice
from .farm import Farm, load_farm
from .settings import SIM_ROOT, Settings, load_settings
from .state import next_boot_ids

log = logging.getLogger("sim")


def make_device(
    entry: Mapping[str, Any],
    site: str,
    boot_id: int,
    farm: Farm,
    alerts: Mapping[str, Any],
    clock: SimClock | None,
) -> SimDevice:
    """The device a layout.json entry describes; `clock` only on playground sites (simTs)."""
    device_id, kind = entry["id"], entry["kind"]
    if kind == "ZONE_CONTROLLER":
        zone = farm.zones.get(entry.get("zone"))
        if zone is None:
            raise ValueError(
                f"layout.json: device {device_id} names zone {entry.get('zone')!r}, "
                f"not one of {sorted(farm.zones)}"
            )
        alarms = zone_alarms(alerts, zone.zone_id, zone.day_tank_l)
        return ZoneDevice(device_id, kind, site, boot_id, zone=zone, alarms=alarms, clock=clock)
    if kind == "CLIMATE_CONTROLLER":
        alarms = climate_alarms(farm.climate.setpoints)
        return ClimateDevice(
            device_id, kind, site, boot_id, climate=farm.climate, alarms=alarms, clock=clock
        )
    if kind == "POWER_MONITOR":
        return PowerDevice(device_id, kind, site, boot_id, power=farm.power, clock=clock)
    return SimDevice(device_id, kind, site, boot_id, clock=clock)


async def run(settings: Settings) -> None:
    site = load_site(settings.client_dir)
    layout_devices = load_devices(settings.client_dir)  # one SimDevice per entry (contract 2.2)
    # The simulated day starts now; scenario start times (contract 11.4) come later.
    clock = SimClock(datetime.now(UTC), settings.sim_speed, load_speed_options(settings.client_dir))
    playground = playground_enabled(settings.client_dir)
    farm = load_farm(settings.client_dir)  # before bootIds: a refused start is not a restart
    alerts = read_pack_file(settings.client_dir, "alerts.json")
    boot_ids = next_boot_ids(
        settings.state_dir / "boot_ids.json", [d["id"] for d in layout_devices]
    )
    devices = [
        make_device(d, site, boot_ids[d["id"]], farm, alerts, clock if playground else None)
        for d in layout_devices
    ]
    tick_loop = TickLoop(clock, farm, [d for d in devices if isinstance(d, ReadingDevice)])
    log.info("playing %s on site %s (bootId %s)", ", ".join(d.id for d in devices), site, boot_ids)
    log.info("clock speed x%g; simTs in messages: %s", clock.speed, "yes" if playground else "no")
    async with asyncio.TaskGroup() as tasks:
        for device in devices:
            tasks.create_task(device.run(settings))
        tasks.create_task(tick_loop.run())


def main() -> None:
    load_dotenv(SIM_ROOT / ".env")
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # paho-mqtt (under aiomqtt) needs a selector event loop; Windows defaults to proactor.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(run(settings))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
