"""Run the farm simulator. From simulator/: python -m sim.main"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .alarms import climate_alarms, zone_alarms
from .clock import SimClock, TickLoop
from .config import (
    load_devices,
    load_min_real_reading_interval_s,
    load_site,
    load_speed_options,
    load_zone_dose,
    load_zone_limits,
    playground_enabled,
    read_pack_file,
)
from .control_api import run_control_api
from .controller.zone import ZoneController
from .devices.base import SimDevice
from .devices.climate import ClimateDevice
from .devices.power import PowerDevice
from .devices.readings import ReadingDevice
from .devices.zone import ZoneDevice
from .farm import Farm, load_farm
from .farm_hal import FarmHal
from .playground import Playground
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
    *,
    client_dir: Path | None = None,
    state_dir: Path | None = None,
) -> SimDevice:
    """The device a layout.json entry describes; `clock` only on playground sites (simTs).

    With client_dir and state_dir, a zone controller also gets its controller and obeys commands.
    """
    device_id, kind = entry["id"], entry["kind"]
    if kind == "ZONE_CONTROLLER":
        zone = farm.zones.get(entry.get("zone"))
        if zone is None:
            raise ValueError(
                f"layout.json: device {device_id} names zone {entry.get('zone')!r}, "
                f"not one of {sorted(farm.zones)}"
            )
        alarms = zone_alarms(alerts, zone.zone_id, zone.day_tank_l)
        device = ZoneDevice(device_id, kind, site, boot_id, zone=zone, alarms=alarms, clock=clock)
        if client_dir is not None and state_dir is not None:
            attach_controller(device, farm, client_dir, state_dir)
        return device
    if kind == "CLIMATE_CONTROLLER":
        alarms = climate_alarms(farm.climate.setpoints)
        return ClimateDevice(
            device_id, kind, site, boot_id, climate=farm.climate, alarms=alarms, clock=clock
        )
    if kind == "POWER_MONITOR":
        return PowerDevice(device_id, kind, site, boot_id, power=farm.power, clock=clock)
    return SimDevice(device_id, kind, site, boot_id, clock=clock)


def attach_controller(device: ZoneDevice, farm: Farm, client_dir: Path, state_dir: Path) -> None:
    """Give a zone device its controller (T3-4), on a HAL over its zone of the farm.

    Its limits are limits.json's until DEVICE_CONFIG (7.7) is handled, so configVersion is null.
    """
    zone = device.zone
    hal = FarmHal(farm, zone, state_dir / f"nvs_{device.id}.json")
    config = {
        "configVersion": None,
        "limits": load_zone_limits(client_dir, zone.zone_id),
        "dose": load_zone_dose(client_dir, zone.zone_id),
    }
    device.controller = ZoneController(
        hal, zone.zone_id, zone.day_tank_l, config, zone.day_tank_usable_l, device.report,
        device_id=device.id, zone_name=zone.name,
    )
    device.sim_now = hal.sim_now
    device.controller.start()


def controllers_of(devices: list[SimDevice]) -> list[ZoneController]:
    return [d.controller for d in devices if isinstance(d, ZoneDevice) and d.controller]


async def run(settings: Settings) -> None:
    site = load_site(settings.client_dir)
    layout_devices = load_devices(settings.client_dir)  # one SimDevice per entry (contract 2.2)
    # The simulated day starts now; scenario start times (contract 11.4) come later.
    clock = SimClock(datetime.now(UTC), settings.sim_speed, load_speed_options(settings.client_dir))
    is_playground = playground_enabled(settings.client_dir)
    farm = load_farm(settings.client_dir)  # before bootIds: a refused start is not a restart
    farm.now = clock.last_tick
    alerts = read_pack_file(settings.client_dir, "alerts.json")
    boot_ids = next_boot_ids(
        settings.state_dir / "boot_ids.json", [d["id"] for d in layout_devices]
    )
    devices = [
        make_device(
            d, site, boot_ids[d["id"]], farm, alerts, clock if is_playground else None,
            client_dir=settings.client_dir, state_dir=settings.state_dir,
        )
        for d in layout_devices
    ]
    min_real_reading_s = load_min_real_reading_interval_s(settings.client_dir)
    playground = None
    if is_playground:  # the farm moves through it, so held values stay put (contract 11.4.2)
        playground = Playground(
            farm, clock, settings.client_dir, controllers=controllers_of(devices),
            device_ids=[d.id for d in devices], min_real_reading_s=min_real_reading_s,
        )
    tick_loop = TickLoop(
        clock,
        playground or farm,
        [d for d in devices if isinstance(d, ReadingDevice)],
        min_real_reading_s=min_real_reading_s,
        controllers=controllers_of(devices),
    )
    log.info("playing %s on site %s (bootId %s)", ", ".join(d.id for d in devices), site, boot_ids)
    sim_ts = "yes" if is_playground else "no"
    log.info("clock speed x%g; simTs in messages: %s", clock.speed, sim_ts)
    async with asyncio.TaskGroup() as tasks:
        for device in devices:
            tasks.create_task(device.run(settings))
        tasks.create_task(tick_loop.run())
        if playground is not None:
            tasks.create_task(
                run_control_api(playground, settings.control_host, settings.control_port)
            )


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
