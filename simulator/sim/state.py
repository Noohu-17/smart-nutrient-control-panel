"""What survives a simulator restart: each device's bootId (contract 5).

bootId must go up by 1 every time a device restarts, so the back end can order
messages by (bootId, seq). Starting the simulator restarts every device it plays.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger("sim.state")


def next_boot_ids(state_file: Path, device_ids: Iterable[str]) -> dict[str, int]:
    """Increment and save the bootId of each device; a new device starts at 1."""
    saved = _read(state_file)
    boot_ids = {device_id: saved.get(device_id, 0) + 1 for device_id in device_ids}
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp = state_file.with_name(state_file.name + ".tmp")
    temp.write_text(json.dumps({**saved, **boot_ids}, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, state_file)  # never leaves a half-written file
    return boot_ids


def _read(state_file: Path) -> dict[str, int]:
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); bootIds restart at 1", state_file, err)
        return {}
    if not isinstance(data, dict):
        log.warning("%s is not a JSON object; bootIds restart at 1", state_file)
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }
