"""Plays a controller test vector (tests/vectors/README.md) against the Python controller.

play() builds a scripted HAL from the vector's start, runs its timeline through handle() and
step(), and returns everything that happened. failures() compares that with what the vector
expects, and always checks what must hold in every vector: the full envelope, (bootId, seq) in
order, and every report's shape. The firmware's runner will read the same files.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.contract import parse_farm_message

from sim.controller.zone import ZoneController
from sim.messages import Envelope

VECTOR_DIR = Path(__file__).resolve().parents[1] / "vectors"
VECTOR_FILES = sorted(VECTOR_DIR.glob("*.json"))
CONTRACT_TIME = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"
ENVELOPE_KEYS = {"v", "type", "msgId", "ts", "site", "device", "bootId", "seq"}  # contract 5
TOPICS = {  # contract 4
    "CMD_STATE": "farm/{site}/zone/{zone}/cmd/state",
    "EVENT": "farm/{site}/zone/{zone}/event",
    "ZONE_STATE": "farm/{site}/zone/{zone}/state",
}
STATES_WITH_REASON = {"REJECTED", "STOPPED", "PARTIAL", "FAILED"}  # contract 6.6
STATES_AFTER_ACCEPTED = {"EXECUTING", "COMPLETED", "STOPPED"}  # contract 8.2


class ScriptedHal:
    """The hardware as a vector describes it, recording every call that changes something."""

    def __init__(self, start: dict[str, Any]):
        self.inputs = dict(start["inputs"])
        self.sensors = {name: dict(reading) for name, reading in start["sensors"].items()}
        self.outputs = dict(start["outputs"])
        self.nvs = json.loads(json.dumps(start["nvs"]))
        self.clock = datetime.fromisoformat(start["wallClockUtc"])
        self.now = 0
        self.calls: list[tuple[int, str]] = []

    def read_sensor(self, sensor_id: str) -> tuple[float | None, str]:
        reading = self.sensors.get(sensor_id)
        return (reading["value"], reading["status"]) if reading else (None, "FAULT")

    def read_input(self, input_id: str) -> bool:
        return bool(self.inputs.get(input_id, False))

    def set_output(self, output_id: str, on: bool) -> None:
        self.outputs[output_id] = on
        self.calls.append((self.now, f"set_output:{output_id}:{'on' if on else 'off'}"))

    def now_ms(self) -> int:
        return self.now

    def utc_ms(self) -> int:
        moment = self.clock + timedelta(milliseconds=self.now)
        return round(moment.astimezone(UTC).timestamp() * 1000)

    def nvs_read(self, key: str) -> dict | None:
        value = self.nvs.get(key)
        return json.loads(json.dumps(value)) if value is not None else None

    def nvs_write(self, key: str, value: dict) -> None:
        self.nvs[key] = json.loads(json.dumps(value))
        self.calls.append((self.now, f"nvs_write:{key}"))

    def relays_drop(self) -> None:
        """A reboot or power cut: every output is off until the controller says otherwise."""
        self.outputs = dict.fromkeys(self.outputs, False)


@dataclass
class Reported:
    at_ms: int
    topic: str
    message: dict[str, Any]


@dataclass
class Run:
    vector: dict[str, Any]
    hal: ScriptedHal
    reported: list[Reported] = field(default_factory=list)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def make_controller(vector: dict[str, Any], hal: ScriptedHal, report: Any) -> ZoneController:
    """The controller a vector's start describes; history at t=0 unless the start says when."""
    start, state = vector["start"], vector["start"]["controller"]
    assert state["operation"] is None, "start an operation with a command in the timeline"
    history = [[0, line, ml] for line, ml in state["dosed24hMl"].items() if ml]
    history += state.get("doseHistory", [])
    return ZoneController(
        hal,
        vector["zone"],
        start["doseLines"],
        start["config"],
        {line: tank["usableL"] for line, tank in start["dayTanks"].items()},
        report,
        device_id=vector["device"],
        stopped=state["stopped"],
        latches=state["latches"],
        dose_history=history,
        recent=state["recentCmdIds"],
    )


def play(vector: dict[str, Any]) -> Run:
    hal = ScriptedHal(vector["start"])
    run = Run(vector, hal)
    site, zone = vector["site"], vector["zone"]
    boot = {"id": 1, "envelope": Envelope(site, vector["device"], 1)}

    def report(msg_type: str, body: dict[str, Any]) -> None:
        ts = hal.clock + timedelta(milliseconds=hal.now)
        message = {**boot["envelope"].next(msg_type, ts), **body}
        topic = TOPICS[msg_type].format(site=site, zone=zone)
        run.reported.append(Reported(hal.now, topic, json.loads(json.dumps(message))))

    controller = make_controller(vector, hal, report)
    entries = {entry["atMs"]: entry for entry in vector["timeline"]}
    for now in range(0, vector["runUntilMs"] + 1, vector["stepPeriodMs"]):
        hal.now = now
        entry = entries.get(now)
        if entry:
            action = entry["action"]
            if action == "SET_INPUTS":
                hal.inputs.update(entry["inputs"])
            elif action == "SET_SENSORS":
                for name, reading in entry["sensors"].items():
                    hal.sensors[name] = dict(reading)
            elif action == "DELIVER_COMMAND":
                controller.handle(entry["message"])
            elif action == "REBOOT":
                hal.relays_drop()
                boot["id"] += 1
                boot["envelope"] = Envelope(site, vector["device"], boot["id"])
                controller = make_rebooted(vector, hal, report)
                controller.restart(entry["reason"])
            else:
                raise ValueError(f"unknown action {action}")
        controller.step(now)
    return run


def make_rebooted(vector: dict[str, Any], hal: ScriptedHal, report: Any) -> ZoneController:
    """A new controller: RAM is gone, and only what is in nvs comes back through restart()."""
    start = vector["start"]
    return ZoneController(
        hal,
        vector["zone"],
        start["doseLines"],
        start["config"],
        {line: tank["usableL"] for line, tank in start["dayTanks"].items()},
        report,
        device_id=vector["device"],
    )


def matches(expected: Any, actual: Any) -> bool:
    """Subset match (README): every listed field equal, objects compared recursively."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches(value, actual[key]) for key, value in expected.items()
        )
    return expected == actual


def _matching(expected: dict[str, Any], reported: list[Reported]) -> list[Reported]:
    found = []
    for item in reported:
        if item.message.get("type") != expected["type"]:
            continue
        if "topic" in expected and item.topic != expected["topic"]:
            continue
        if matches(expected.get("match", {}), item.message):
            found.append(item)
    return found


def failures(run: Run) -> list[str]:
    """Everything that differs from what the vector says must happen. Empty means it passed."""
    return (
        _timeline_failures(run)
        + _never_failures(run)
        + _final_failures(run)
        + _shape_failures(run)
    )


def _timeline_failures(run: Run) -> list[str]:
    problems = []
    timeline = run.vector["timeline"]
    ends = [entry["atMs"] for entry in timeline[1:]] + [run.vector["runUntilMs"] + 1]
    for entry, end in zip(timeline, ends, strict=True):
        start = entry["atMs"]
        window = [item for item in run.reported if start <= item.at_ms < end]
        for expected in entry["expect"]["messages"]:
            found = _matching(expected, window)
            count = expected.get("count")
            if (count is None and not found) or (count is not None and len(found) != count):
                problems.append(
                    f"at {start} ms: expected {count if count is not None else 'at least 1'} "
                    f"{expected['type']} {expected.get('match', {})}, found {len(found)}; "
                    f"reported: {[_short(item.message) for item in window]}"
                )
            for item in found:
                problems += [
                    f"at {start} ms: {expected['type']}.{name} must be a non-empty string"
                    for name in expected.get("nonEmpty", [])
                    if not (isinstance(item.message.get(name), str) and item.message[name])
                ]
        calls = [call for at, call in run.hal.calls if start <= at < end]
        wanted = entry["expect"].get("calls", [])
        remaining = iter(calls)
        if not all(call in remaining for call in wanted):  # in order, others may come between
            problems.append(f"at {start} ms: expected calls {wanted} in order, got {calls}")
    return problems


def _never_failures(run: Run) -> list[str]:
    never, problems = run.vector["never"], []
    calls = [call for _, call in run.hal.calls]
    problems += [
        f"{output} was switched on" for output in never["outputsOn"]
        if f"set_output:{output}:on" in calls
    ]
    problems += [
        f"{output} was switched off" for output in never["outputsOff"]
        if f"set_output:{output}:off" in calls
    ]
    for expected in never["messages"]:
        if _matching(expected, run.reported):
            problems.append(f"reported what must never be: {expected}")
    return problems


def _final_failures(run: Run) -> list[str]:
    final, problems = run.vector["final"], []
    if run.hal.outputs != final["outputs"]:
        problems.append(f"final outputs {run.hal.outputs}, expected {final['outputs']}")
    wanted = final.get("lastZoneState")
    if wanted is not None:
        states = [item.message for item in run.reported if item.message["type"] == "ZONE_STATE"]
        if not states or not matches(wanted, states[-1]):
            problems.append(f"last ZONE_STATE {states[-1:]}, expected {wanted}")
    return problems


def _shape_failures(run: Run) -> list[str]:
    """What must hold for every report, whatever the vector tests (contract 5, 6)."""
    problems, last_order = [], None
    states_seen = _states_from_the_start(run.vector["start"])
    for item in run.reported:
        message = item.message
        missing = ENVELOPE_KEYS - message.keys()
        if missing:
            problems.append(f"{message['type']} is missing {sorted(missing)}")
            continue
        ts_ok = re.fullmatch(CONTRACT_TIME, message["ts"])
        if uuid.UUID(message["msgId"]).version != 4 or not ts_ok:
            problems.append(f"bad msgId or ts in {message}")
        order = (message["bootId"], message["seq"])
        if last_order is not None and order <= last_order:
            problems.append(f"(bootId, seq) went from {last_order} to {order}")
        last_order = order
        kind = message["type"]
        if kind == "ZONE_STATE":
            parse_farm_message(message)  # raises if it breaks Track 1's model
        elif kind == "EVENT":
            problems += [
                f"EVENT {message.get('kind')} has no {name}"
                for name in ("message", "systemAction", "nextStep")
                if not message.get(name)
            ]
        elif kind == "CMD_STATE":
            problems += _cmd_state_problems(message, states_seen)
    return problems


def _states_from_the_start(start: dict[str, Any]) -> dict[str, set[str]]:
    """Commands the controller already knows when the vector begins."""
    seen = {item["cmdId"]: {item["state"]} for item in start["controller"]["recentCmdIds"]}
    journal = start["nvs"].get("journal") or {}
    if "cmdId" in journal:
        seen[journal["cmdId"]] = {journal["state"]}
    return seen


def _cmd_state_problems(message: dict[str, Any], seen: dict[str, set[str]]) -> list[str]:
    """Contract 6.6: a reason only where it belongs; REJECTED alone; nothing before ACCEPTED."""
    problems, state = [], message["state"]
    if (message["reason"] is not None) != (state in STATES_WITH_REASON):
        problems.append(f"CMD_STATE {state} with reason {message['reason']}")
    earlier = seen.setdefault(message["cmdId"], set())
    if state == "REJECTED" and "ACCEPTED" in earlier:
        problems.append(f"{message['cmdId']} was REJECTED after ACCEPTED")
    if state in STATES_AFTER_ACCEPTED and not earlier:
        problems.append(f"{message['cmdId']} went to {state} with no ACCEPTED first")
    earlier.add(state)
    return problems


def _short(message: dict[str, Any]) -> str:
    detail = message.get("state") or message.get("kind") or ""
    return f"{message['type']}:{detail}:{message.get('reason', '')}"
