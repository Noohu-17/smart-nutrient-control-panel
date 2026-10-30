"""Run the farm simulator. From simulator/: python -m sim.main"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from .clock import SimClock
from .config import load_devices, load_site, load_speed_options, playground_enabled
from .devices.base import SimDevice
from .settings import SIM_ROOT, Settings, load_settings
from .state import next_boot_ids

log = logging.getLogger("sim")


async def run(settings: Settings) -> None:
    site = load_site(settings.client_dir)
    layout_devices = load_devices(settings.client_dir)  # one SimDevice per entry (contract 2.2)
    # The simulated day starts now; scenario start times (contract 11.4) come later.
    clock = SimClock(datetime.now(UTC), settings.sim_speed, load_speed_options(settings.client_dir))
    playground = playground_enabled(settings.client_dir)
    boot_ids = next_boot_ids(
        settings.state_dir / "boot_ids.json", [d["id"] for d in layout_devices]
    )
    devices = [
        SimDevice(d["id"], d["kind"], site, boot_ids[d["id"]], clock=clock if playground else None)
        for d in layout_devices
    ]
    log.info("playing %s on site %s (bootId %s)", ", ".join(d.id for d in devices), site, boot_ids)
    log.info("clock speed x%g; simTs in messages: %s", clock.speed, "yes" if playground else "no")
    async with asyncio.TaskGroup() as tasks:
        for device in devices:
            tasks.create_task(device.run(settings))


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
