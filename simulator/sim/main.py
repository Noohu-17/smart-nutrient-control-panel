"""Run the farm simulator. From simulator/: python -m sim.main"""

from __future__ import annotations

import asyncio
import logging
import sys

from dotenv import load_dotenv

from .devices.base import connect_forever
from .settings import SIM_ROOT, Settings, load_settings

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
    async with asyncio.TaskGroup() as tasks:
        for device in DEVICES:
            tasks.create_task(connect_forever(settings, device["id"]))


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
        logging.getLogger("sim").info("stopped")


if __name__ == "__main__":
    main()
