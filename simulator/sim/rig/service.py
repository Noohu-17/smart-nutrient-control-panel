"""The zone controller service (T1-32): sim/controller's zone controller, on the prototype rig.

For each zone io_map.json puts on the rig, it runs that zone's controller (ZC1 ...) as the
simulator does, on a HAL that talks to the rig instead of the farm model, and speaks the contract
on the broker. The back end and the app see the same messages as from any zone controller.

    python -m sim.rig        (from simulator/; settings: simulator/readme.md, sim/rig/README.md)

Its zones are LIVE (layout.json source, contract 2.4): the simulator never plays a live zone's
controller, so the two are never both ZC1.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from ..clock import CONTROL_STEP_S
from ..config import load_site
from ..controller.zone import ZoneController
from ..settings import SIM_ROOT, Settings, load_settings
from ..state import next_boot_ids
from .config import RigSettings, RigZone, load_rig_settings, load_rig_zones
from .device import RigZoneDevice
from .hal import RigHal
from .link import RigLink

log = logging.getLogger("sim.rig")


class NoRigZones(ValueError):
    """The client's pack puts no zone on the rig: nothing for this service to run."""


def build(settings: Settings, rig: RigSettings) -> tuple[RigLink, list[RigZoneDevice]]:
    """The link and one device per rig zone, each with its controller, started."""
    zones = load_rig_zones(settings.client_dir)
    if not zones:
        raise NoRigZones(
            f"io_map.json in {settings.client_dir} has no controller with link SF_GATEWAY: "
            "no zone runs on the rig"
        )
    site = load_site(settings.client_dir)
    state_dir = settings.state_dir / "rig"  # its own nvs and bootIds, never the simulator's
    boot_file = state_dir / "boot_ids.json"
    boot_ids = next_boot_ids(boot_file, [zone.device_id for zone in zones])
    link = RigLink(rig)
    for zone in zones:
        link.devices.append(_device(zone, site, boot_ids[zone.device_id], state_dir, link, rig))
    return link, link.devices


def _device(
    zone: RigZone, site: str, boot_id: int, state_dir: Path, link: RigLink, rig: RigSettings
) -> RigZoneDevice:
    boot_file = state_dir / "boot_ids.json"
    hal = RigHal(zone, state_dir / f"nvs_{zone.device_id}.json", link.send)
    device = RigZoneDevice(
        zone, site, boot_id, hal, offline_after_s=rig.offline_after_s,
        next_boot_id=lambda: next_boot_ids(boot_file, [zone.device_id])[zone.device_id],
    )
    config = {"configVersion": None, "limits": zone.limits, "dose": zone.dose}
    device.controller = ZoneController(
        hal, zone.zone_id, zone.lines, config, {}, device.report,
        device_id=zone.device_id, zone_name=zone.name,
        unmonitored_tanks=() if zone.day_tank_level_sensors else zone.lines,
        refill_available=False,  # the rig's inlet runs on its own auto-fill
    )
    device.controller.start()  # a new run of the service: an interrupted command is closed
    return device


async def tick_forever(devices: list[RigZoneDevice], step_s: float = CONTROL_STEP_S) -> None:
    """Notice rig silence and step each controller, on real time."""
    while True:
        for device in devices:
            device.tick()
        await asyncio.sleep(step_s)


async def run(settings: Settings, rig: RigSettings) -> None:
    link, devices = build(settings, rig)
    log.info("zone controller service: %s on the rig (%s, commands on %s)",
             ", ".join(f"{d.id}={d.zone.zone_id}@{d.zone.gateway}:zone{d.zone.rig_zone}"
                       for d in devices), rig.telemetry_topic, rig.command_topic)
    async with asyncio.TaskGroup() as tasks:
        for device in devices:
            tasks.create_task(device.run(settings))
        tasks.create_task(link.run(settings))
        tasks.create_task(tick_forever(devices))


def main() -> None:
    load_dotenv(SIM_ROOT / ".env")
    settings, rig = load_settings(), load_rig_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None  # for paho
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(run(settings, rig))
    except NoRigZones as err:  # a clean exit, so Compose's restart: on-failure leaves it stopped
        log.warning("%s; the zone controller service has nothing to run and stops", err)
    except KeyboardInterrupt:
        log.info("stopped")
