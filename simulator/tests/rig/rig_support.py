"""Helpers for the zone controller service's tests (T1-32), shared by tests/rig.

`make_rig_pack` is client_A with ZC1 on the rig (io_map.json), the way client_B will put its zones
on it (T1-31). `recording` replays recordings/*.jsonl: a gateway session written from the rig's
own documented examples and schemas (snm-prototype-extension-v1, gateway docs/CLOUD.md) until
R-10's capture of the real rig replaces it.
"""

from __future__ import annotations

import copy
import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sim.controller.zone import ZoneController
from sim.rig.config import load_rig_zones
from sim.rig.hal import RigHal
from sim.settings import load_settings

RECORDINGS = Path(__file__).parent / "recordings"
SESSION = "gateway-01-session.jsonl"
T0 = 1793952000  # the recording's own time
RIG_ENTRY = {
    "link": "SF_GATEWAY",
    "gateway": "gateway-01",
    "rigZone": 1,
    "pumps": {"A": "relay1", "B": "relay2", "ACID": "relay3"},
    "tdsPpmPerMsCm": 500,
    "dayTankLevelSensors": False,
}


def make_rig_pack(tmp_path: Path, **entry_changes) -> Path:
    pack = shutil.copytree(load_settings({}).client_dir, tmp_path / "client")
    io_map = json.loads((pack / "io_map.json").read_text(encoding="utf-8"))
    io_map["controllers"] = {"ZC1": {**RIG_ENTRY, **entry_changes}}
    (pack / "io_map.json").write_text(json.dumps(io_map), encoding="utf-8")
    return pack


def recording(name: str = SESSION) -> list[dict]:
    """[{"subtopic", "retained", "payload"}, ...] in the order the gateway sent them."""
    lines = (RECORDINGS / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def zone_doc(*, ts: int, seq: int = 42, **parts) -> dict:
    """The session's first live Zone 1 update at `ts`, with parts changed, e.g.
    water_quality={"ph": None}, actuators={"estop": True}."""
    first = next(r["payload"] for r in recording()
                 if r["subtopic"] == "sf/zone/1" and not r["retained"])
    document = copy.deepcopy(first)
    document.update(ts=ts, seq=seq)
    for part, changes in parts.items():
        if isinstance(document.get(part), dict) and isinstance(changes, dict):
            document[part].update(changes)
        else:
            document[part] = changes
    return document


class Clock:
    """Wall and monotonic time, moved by hand: one real second is one second of both."""

    def __init__(self, unix: float = T0 + 2):
        self.unix = unix

    def utc(self) -> datetime:
        return datetime.fromtimestamp(self.unix, UTC)

    def mono(self) -> float:
        return self.unix

    def advance(self, seconds: float) -> None:
        self.unix += seconds


class Rig:
    """sim/controller's ZoneController on the rig's HAL, as the service builds it, with every
    sf command it sends and everything it reports kept for the test."""

    def __init__(self, tmp_path: Path, pack: Path, clock: Clock):
        self.zone = load_rig_zones(pack)[0]
        self.clock, self.sent, self.reported = clock, [], []
        self.hal = RigHal(self.zone, tmp_path / "nvs_ZC1.json", self.sent.append,
                          utc_now=clock.utc, monotonic=clock.mono)
        self.hal.update(zone_doc(ts=int(clock.unix)))
        config = {"configVersion": None, "limits": self.zone.limits, "dose": self.zone.dose}
        self.controller = ZoneController(
            self.hal, self.zone.zone_id, self.zone.lines, config, {},
            lambda kind, body: self.reported.append((kind, json.loads(json.dumps(body)))),
            device_id="ZC1", zone_name=self.zone.name, unmonitored_tanks=self.zone.lines,
            refill_available=False,
        )

    def step(self, seconds: float = 0.5) -> None:
        self.clock.advance(seconds)
        self.hal.update(zone_doc(ts=int(self.clock.unix), seq=int(self.clock.unix)))  # fresh
        self.controller.step(self.hal.now_ms())

    def command(self, kind: str, **body) -> str:
        cmd_id = str(uuid.uuid4())
        expires = self.clock.utc() + timedelta(seconds=60)
        self.controller.handle({
            "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "cmdId": cmd_id, "kind": kind,
            "site": "siet-ph1", "device": "CLOUD",
            "expiresAt": f"{expires:%Y-%m-%dT%H:%M:%S}.000Z", "target": {"zone": "Z1"}, **body,
        })
        return cmd_id

    def dose(self, **lines) -> str:
        return self.command("DOSE", lines=[{"line": k, "ml": v} for k, v in lines.items()],
                            order=list(lines), mixWaitS=0, maxDurationS=600, tolerancePct=5)

    def doses_sent(self) -> list[dict]:
        return [c for c in self.sent if c["cmd"] == "dose"]

    def rig_reports(self, command: dict, state: str, *, elapsed: float = 0.0,
                    reason: str | None = None) -> None:
        """The rig's sf/dose/1 for a dose command it was sent."""
        args = command["args"]
        self.hal.on_dose_report({
            "schema": "sf/dose/1", "gateway": "gateway-01", "boot": 7, "seq": 1, "zone": 1,
            "device": "actuator-node-01", "dose_id": args["dose_id"], "relay": args["relay"],
            "state": state, "reason": reason, "duration_s": args["seconds"],
            "elapsed_s": elapsed, "ts": int(self.clock.unix),
        })

    def states(self, cmd_id: str) -> list[dict]:
        return [b for k, b in self.reported if k == "CMD_STATE" and b["cmdId"] == cmd_id]
