"""Contract messages: the envelope (section 5) and DEVICE_STATUS (6.4).

Messages are plain dicts built here, field for field as the contract says.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

SIM_FIRMWARE = "sim-0.1.0"


def utc_ts(moment: datetime) -> str:
    """Contract time: ISO-8601 UTC with milliseconds and Z, e.g. 2026-11-09T04:54:00.000Z."""
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def status_topic(site: str, device_id: str) -> str:
    return f"farm/{site}/device/{device_id}/status"


class Envelope:
    """Header for every message one device sends during one boot (contract 5).

    seq counts every message of this boot, whatever its type; (bootId, seq)
    gives the back end exact order and shows gaps.
    """

    def __init__(self, site: str, device_id: str, boot_id: int):
        self.site, self.device_id, self.boot_id = site, device_id, boot_id
        self.seq = 0

    def next(self, msg_type: str, ts: datetime, sim_ts: datetime | None = None) -> dict[str, Any]:
        """ts is real time; sim_ts (playground sites only) becomes simTs."""
        self.seq += 1
        header = {
            "v": 1,
            "type": msg_type,
            "msgId": str(uuid.uuid4()),
            "ts": utc_ts(ts),
            "site": self.site,
            "device": self.device_id,
            "bootId": self.boot_id,
            "seq": self.seq,
        }
        if sim_ts is not None:
            header["simTs"] = utc_ts(sim_ts)
        return header


def device_status(envelope: dict[str, Any], *, uptime_s: int, gateway: bool) -> dict[str, Any]:
    """DEVICE_STATUS (6.4) for a simulated device.

    A simulated device has no radio, heap or firmware build, so those fields
    are null rather than invented numbers (contract 1: never a fake value).
    configVersion stays null until DEVICE_CONFIG (7.7) is handled. GW1 also
    reports its buffer and cloud link (10, rule 5).
    """
    status = {
        **envelope,
        "online": True,
        "fw": SIM_FIRMWARE,
        "fwHash": None,
        "uptimeS": uptime_s,
        "rssiDbm": None,
        "clockSynced": True,
        "configVersion": None,
        "freeHeapKb": None,
    }
    if gateway:
        status.update(bufferedMessages=0, cloudLink="UP")
    return status


def offline_status(device_id: str) -> dict[str, Any]:
    """The last-will payload, exactly as contract 6.4 gives it."""
    return {"v": 1, "type": "DEVICE_STATUS", "device": device_id, "online": False}
