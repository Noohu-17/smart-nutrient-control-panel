"""The prototype rig's own messages, "sf schema 1" (snm-prototype-extension-v1: gateway CLOUD.md).

The gateway publishes on `<telemetry topic>/sf/...` and takes commands on `<command topic>/sf/cmd`.
Both topic bases are `m/<workspace>/c/<channel>` on its broker; they come from the environment,
never from git. Everything here follows the gateway's JSON schemas (docs/schemas/).
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

ZONE, HEARTBEAT, DOSE, CMD_RESULT = "sf/zone/1", "sf/heartbeat/1", "sf/dose/1", "sf/cmd-result/1"
STATUS_SUBTOPIC = "sf/gateway/status"  # SenML online / offline; the gateway's last will
COMMAND_SUBTOPIC = "sf/cmd"
DOSE_SECONDS = (0.1, 600.0)  # sf/cmd/1: a timed dose runs 0.1-600 s
PART_STALE_S = 30  # a part of a zone update older than this isn't used (CLOUD.md section 2)
OFFLINE_AFTER_S = 90.0  # 3 missed heartbeats (CLOUD.md section 3)
FINISHED = ("done", "stopped", "rejected")  # sf/dose/1 states after "running"
# sf/cmd-result/1: anything else ran nothing. "duplicate" is the rig already holding this id (a
# command sent again, or re-delivered after a gateway restart): its sf/dose/1 says what happened.
COMMAND_ACCEPTED = ("ok", "accepted", "partial", "duplicate")


def subtopic(topic: str, base: str) -> str | None:
    """`sf/zone/1` from `<base>/sf/zone/1`; None for a topic outside the base."""
    prefix = base.rstrip("/") + "/"
    return topic[len(prefix):] if topic.startswith(prefix) else None


def parse(payload: bytes | str) -> Any:
    """The JSON in a payload, or None."""
    try:
        return json.loads(payload)
    except ValueError:
        return None


def status_of(document: Any) -> tuple[str, bool] | None:
    """(gateway, online) from its SenML status: `[{"bn": "gateway-01:", "n": "status", ...}]`."""
    if not isinstance(document, list):
        return None
    for record in document:
        if isinstance(record, dict) and record.get("n") == "status":
            gateway = str(record.get("bn", "")).rstrip(":")
            if record.get("vs") in ("online", "offline") and gateway:
                return gateway, record["vs"] == "online"
    return None


def dose_id_for(run_id: str) -> str:
    """A dose id the gateway accepts (`^[A-Za-z0-9_-]{1,32}$`) from a controller's run id
    (cmdId/line). The same run always gets the same id, and the gateway never runs a dose id
    twice, so a command repeated after a restart can't dose again."""
    return hashlib.sha256(run_id.encode()).hexdigest()[:32]


def dose_command(
    *, dose_id: str, target: str, relay: str, seconds: float, issued_at: int
) -> dict[str, Any]:
    """sf/cmd/1 `dose`: run one relay for `seconds`. The command id is the dose id."""
    return {
        "schema": "sf/cmd/1",
        "id": dose_id,
        "target": target,
        "cmd": "dose",
        "origin": "zone-controller",
        "issued_at": issued_at,
        "args": {"relay": relay, "seconds": round(seconds, 3), "dose_id": dose_id},
    }


def switch_off_command(*, command_id: str, target: str, relays: list[str], issued_at: int) -> dict:
    """sf/cmd/1 `switch` OFF: a dose running on one of these relays ends ("stopped", "command")."""
    return {
        "schema": "sf/cmd/1",
        "id": command_id,
        "target": target,
        "cmd": "switch",
        "origin": "zone-controller",
        "issued_at": issued_at,
        "args": {"relays": dict.fromkeys(relays, False)},
    }


class Repeats:
    """QoS 1 can deliver twice; (gateway, boot, schema, seq) is unique per message (CLOUD.md 1)."""

    def __init__(self, size: int = 1024):
        self._seen: set[tuple[Any, ...]] = set()
        self._order: deque[tuple[Any, ...]] = deque()
        self._size = size

    def seen(self, document: dict[str, Any]) -> bool:
        key = tuple(document.get(k) for k in ("gateway", "boot", "schema", "seq"))
        if None in key:
            return False
        if key in self._seen:
            return True
        self._seen.add(key)
        self._order.append(key)
        if len(self._order) > self._size:
            self._seen.discard(self._order.popleft())
        return False
