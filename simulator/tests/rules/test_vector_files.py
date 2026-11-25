"""Structure and contract checks for the controller test vectors (T3-3).

These tests don't run the vectors: test_vectors.py does. They check that every
vector is valid JSON, uses the contract's names, IDs, topics and enums, has a
consistent timeline, and is listed in TODO.md. The enum lists are copied from
contract v1.0; when the contract's JSON Schemas land in docs/contract/schemas/,
use those instead.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

VECTOR_DIR = Path(__file__).resolve().parents[1] / "vectors"
VECTOR_FILES = sorted(VECTOR_DIR.glob("*.json"))
CATALOGUE = VECTOR_DIR / "TODO.md"

# Contract v1.0 enums.
CMD_STATES = {  # 6.6
    "ACCEPTED", "EXECUTING", "COMPLETED", "PARTIAL", "FAILED", "STOPPED", "REJECTED",
}
FARM_REASONS = {  # 8.4, codes a controller may raise
    "ESTOP", "NOT_REMOTE", "BUSY", "LIMIT_DOSE", "LIMIT_24H", "DAYTANK_LOW", "RESERVOIR_LOW",
    "SENSOR_STALE", "EXPIRED_ON_ARRIVAL", "NO_FLOW", "FLOW_SHORT", "TIMEOUT", "STOP_REQUESTED",
    "ESTOP_DURING", "MODE_CHANGED_DURING", "FAULT_DURING", "REBOOT_MID_DOSE", "NOT_MANUAL",
    "NOT_INSTALLED",
    "NEEDS_FANS", "FEATURE_OFF", "CIRC_PROTECTED", "INVALID_COMMAND", "ZONE_STOPPED",
    "FAULT_LATCHED",
}
EVENT_KINDS = {  # 6.5
    "ESTOP_PRESSED", "ESTOP_RELEASED", "MODE_CHANGED", "LEAK_DETECTED", "LEAK_CLEARED",
    "CIRC_NO_FLOW", "CIRC_FLOW_RESTORED", "SENSOR_FAULT", "SENSOR_RECOVERED", "RESERVOIR_LOW",
    "DAYTANK_LOW", "PUMP_FAULT", "CHILLER_FAULT", "CLIMATE_MODE_REVERTED", "REBOOTED",
}
COMMAND_KINDS = {  # 7
    "DOSE", "REFILL", "STOP", "CLIMATE_MODE", "CLIMATE_EQUIPMENT", "RESUME", "CLEAR_FAULT",
}
COMMAND_TOPICS = {  # 4; climate's RESUME and CLEAR_FAULT go to its cmd topic (7.8, 7.9)
    "DOSE": "farm/{site}/zone/{zone}/cmd",
    "REFILL": "farm/{site}/zone/{zone}/cmd",
    "STOP": "farm/{site}/stop",
    "CLIMATE_MODE": "farm/{site}/climate/cmd",
    "CLIMATE_EQUIPMENT": "farm/{site}/climate/cmd",
    "RESUME": "farm/{site}/zone/{zone}/cmd",
    "CLEAR_FAULT": "farm/{site}/zone/{zone}/cmd",
}
REPORT_TOPICS = {  # 4
    "ZC": {
        "ZONE_TELEMETRY": "farm/{site}/zone/{zone}/telemetry",
        "ZONE_STATE": "farm/{site}/zone/{zone}/state",
        "EVENT": "farm/{site}/zone/{zone}/event",
        "CMD_STATE": "farm/{site}/zone/{zone}/cmd/state",
    },
    "CC": {
        "CLIMATE_TELEMETRY": "farm/{site}/climate/telemetry",
        "CLIMATE_STATE": "farm/{site}/climate/state",
        "EVENT": "farm/{site}/climate/event",
        "CMD_STATE": "farm/{site}/climate/cmd/state",
    },
}
ACTIONS = {"SET_INPUTS", "SET_SENSORS", "DELIVER_COMMAND", "REBOOT"}
REQUIRED_KEYS = {
    "vectorFormat", "id", "title", "contract", "device", "site", "zone", "stepPeriodMs",
    "runUntilMs", "assumptions", "start", "timeline", "never", "final",
}
COMMAND_EXPIRY = timedelta(seconds=60)  # contract 14: expiry 60 s from issuedAt
STOP_EXPIRY = timedelta(minutes=5)  # contract 7.4: ALL_OPERATIONS and ZONE
HAL_CALL = r"nvs_write:\w+|set_output:\w+:(on|off)"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def utc(text: str) -> datetime:
    contract_time = r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z"
    assert re.fullmatch(contract_time, text), f"not contract time: {text}"
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def is_uuid4(text: str) -> bool:
    try:
        parsed = uuid.UUID(text)
    except (ValueError, TypeError):
        return False
    return parsed.version == 4 and str(parsed) == text


def device_family(device: str) -> str:
    assert re.fullmatch(r"(ZC|CC)\d+", device), f"not a controller device ID: {device}"
    return device[:2]


def catalogue_rows() -> list[list[str]]:
    rows = []
    for line in CATALOGUE.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.fullmatch(r"`[\w-]+\.json`", cells[1]):
            rows.append(cells)
    return rows


def test_there_is_at_least_one_vector():
    assert VECTOR_FILES, f"no vectors in {VECTOR_DIR}"


def test_vector_ids_are_unique():
    ids = [load(p)["id"] for p in VECTOR_FILES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("path", VECTOR_FILES, ids=lambda p: p.name)
def test_vector_is_well_formed_and_matches_the_contract(path: Path):
    vector = load(path)
    assert vector.keys() >= REQUIRED_KEYS, f"missing: {REQUIRED_KEYS - vector.keys()}"
    assert "status" not in vector, "pass/fail comes from the runner, never from the file"

    site, zone, family = vector["site"], vector["zone"], device_family(vector["device"])
    assert re.fullmatch(r"[a-z0-9-]+", site)
    assert re.fullmatch(r"Z\d+", zone)
    start = vector["start"]
    clock = utc(start["wallClockUtc"])
    lines = start["doseLines"]
    period, run_until = vector["stepPeriodMs"], vector["runUntilMs"]
    assert isinstance(period, int) and period > 0
    assert vector["assumptions"], "list the assumptions a reviewer could disagree with"

    known_cmd_ids = {item["cmdId"] for item in start["controller"]["recentCmdIds"]}
    known_cmd_ids |= {start["nvs"]["journal"]["cmdId"]} if "journal" in start["nvs"] else set()
    last_at = -1
    for entry in vector["timeline"]:
        at = entry["atMs"]
        assert last_at < at < run_until and at % period == 0, f"bad atMs {at}"
        last_at = at
        action = entry["action"]
        assert action in ACTIONS
        if action == "SET_INPUTS":
            assert entry["inputs"].keys() <= start["inputs"].keys()
        elif action == "SET_SENSORS":
            assert entry["sensors"].keys() <= start["sensors"].keys()
        elif action == "REBOOT":
            assert entry["reason"], "a reboot says why"
        else:
            known_cmd_ids.add(
                check_command(entry, site, zone, lines, clock + timedelta(milliseconds=at))
            )
        for expected in entry["expect"]["messages"]:
            check_expected_message(expected, site, zone, family)
        for call in entry["expect"].get("calls", []):
            assert re.fullmatch(HAL_CALL, call), f"not a HAL call: {call}"

    for expected in vector["never"]["messages"]:
        check_expected_message(expected, site, zone, family, needs_topic=False)
    outputs = start["outputs"].keys()
    assert set(vector["never"]["outputsOn"]) | set(vector["never"]["outputsOff"]) <= outputs
    assert vector["final"]["outputs"].keys() == outputs

    referenced = {
        e["match"]["cmdId"]
        for entry in vector["timeline"]
        for e in entry["expect"]["messages"] + vector["never"]["messages"]
        if "cmdId" in e.get("match", {})
    }
    assert referenced <= known_cmd_ids, "expectations name a cmdId no command carries"


def check_command(entry: dict, site: str, zone: str, lines: list[str], arrival: datetime) -> str:
    cmd = entry["message"]
    assert cmd["v"] == 1 and cmd["type"] == "CMD" and cmd["device"] == "CLOUD"
    assert cmd["site"] == site
    assert is_uuid4(cmd["msgId"]) and is_uuid4(cmd["cmdId"]), "IDs must be UUID v4 (contract 1)"
    assert cmd["kind"] in COMMAND_KINDS
    issued = utc(cmd["issuedAt"])
    assert utc(cmd["ts"]) <= arrival and issued <= arrival
    assert utc(cmd["expiresAt"]) - issued == expected_expiry(cmd)
    pattern = COMMAND_TOPICS.get(cmd["kind"], "farm/{site}/")
    assert entry["topic"].startswith(pattern.format(site=site, zone=zone))
    if cmd["kind"] == "DOSE" and not entry.get("malformed"):
        assert cmd["target"] == {"zone": zone}
        names = [item["line"] for item in cmd["lines"]]
        assert set(names) <= set(lines), "dose names a line this zone doesn't have"
        assert sorted(cmd["order"]) == sorted(names)
        for item in cmd["lines"]:
            assert isinstance(item["ml"], int) and item["ml"] >= 0, "volumes are whole mL"
    return cmd["cmdId"]


def expected_expiry(cmd: dict) -> timedelta:
    scope = cmd["target"].get("scope")
    return STOP_EXPIRY if cmd["kind"] == "STOP" and scope != "ALL_EQUIPMENT" else COMMAND_EXPIRY


def check_expected_message(
    expected: dict, site: str, zone: str, family: str, needs_topic: bool = True
) -> None:
    topics = REPORT_TOPICS[family]
    kind = expected["type"]
    assert kind in topics, f"{kind} is not a message this controller publishes"
    if needs_topic:
        assert expected["topic"] == topics[kind].format(site=site, zone=zone)
    match = expected.get("match", {})
    if kind == "CMD_STATE":
        assert match.get("state") in CMD_STATES
        assert match.get("reason") is None or match["reason"] in FARM_REASONS
        assert "cmdId" not in match or is_uuid4(match["cmdId"])
    if kind == "EVENT":
        assert match.get("kind") in EVENT_KINDS
    if "count" in expected:
        assert isinstance(expected["count"], int) and expected["count"] >= 1


def test_catalogue_matches_the_vector_files():
    rows = catalogue_rows()
    assert rows, "TODO.md has no catalogue rows"
    ids = [row[0] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate IDs in TODO.md"
    for row in rows:
        assert row[-1].startswith(("TODO", "WRITTEN")), f"{row[0]}: status must be TODO or WRITTEN"

    written = {row[1].strip("`"): row[0] for row in rows if row[-1].startswith("WRITTEN")}
    on_disk = {p.name: load(p)["id"] for p in VECTOR_FILES}
    assert written == on_disk, "every vector file is listed as WRITTEN with its ID, and only those"
