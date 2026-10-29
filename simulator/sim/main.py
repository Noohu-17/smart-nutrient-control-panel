"""Run the farm simulator. From simulator/: python -m sim.main"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

from .config import load_site
from .devices.base import SimDevice
from .settings import SIM_ROOT, Settings, load_settings
from .state import next_boot_ids

log = logging.getLogger("sim")

# The devices the simulator plays (contract 2.2), in layout.json's "devices" shape.
# Temporary: T4-4 replaces this with the list read from the client's layout.json.
DEVICES = (
    {"id": "GW1", "kind": "GATEWAY"},
    {"id": "ZC1", "kind": "ZONE_CONTROLLER"},
    {"id": "ZC2", "kind": "ZONE_CONTROLLER"},
    {"id": "CC1", "kind": "CLIMATE_CONTROLLER"},
    {"id": "PM1", "kind": "POWER_MONITOR"},
)


async def run(settings: Settings) -> None:
    site = load_site(settings.client_dir)
    boot_ids = next_boot_ids(settings.state_dir / "boot_ids.json", [d["id"] for d in DEVICES])
    devices = [SimDevice(d["id"], d["kind"], site, boot_ids[d["id"]]) for d in DEVICES]
    log.info("playing %s on site %s (bootId %s)", ", ".join(d.id for d in devices), site, boot_ids)
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
