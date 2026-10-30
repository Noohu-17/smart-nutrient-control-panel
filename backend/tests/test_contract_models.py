"""Contract message models (app/contract) against the examples in docs/contract.md.

The examples are read from the contract itself, so these tests follow it when it
changes: every JSON example of the envelope (§5) and of the §6 messages modelled here
must validate, and must not use a field the models don't know.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.contract import (
    DeviceStatus,
    DeviceStatusLastWill,
    Envelope,
    ZoneTelemetry,
    parse_farm_message,
)

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contract.md"
MODELLED_TYPES = ("ZONE_TELEMETRY", "CLIMATE_TELEMETRY", "POWER_STATE", "DEVICE_STATUS", "ZONE_STATE")

# The contract shortens some values to "…". These stand in for them.
ELIDED_ID = "00000000-0000-4000-8000-000000000000"
ELIDED_TIME = "2026-11-09T04:54:00.000Z"


def _contract_examples() -> list[tuple[str, str, dict]]:
    """(section, heading type, example) for every example of a modelled message type.

    Takes fenced ```json blocks and inline `{…}` code (the last will in §6.4) from
    sections 5 and 6.
    """
    examples = []
    section, heading_type, fence = "", None, None
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        if fence is not None:
            if line.strip() == "```":
                examples.append((section, heading_type, "\n".join(fence)))
                fence = None
            else:
                fence.append(line)
            continue
        if heading := re.match(r"#{2,3} (\d+(?:\.\d+)?)\.? ", line):
            section = heading.group(1)
            named = re.search(r"`([A-Z_]+)`", line)
            heading_type = named.group(1) if named else None
        elif line.strip() == "```json":
            fence = []
        else:
            examples += [(section, heading_type, code) for code in re.findall(r"`(\{[^`]*\})`", line)]

    found = []
    for section, heading_type, text in examples:
        if section.split(".")[0] not in ("5", "6"):
            continue
        kind = re.search(r'"type"\s*:\s*"([A-Z_]+)"', text)
        if kind and kind.group(1) in MODELLED_TYPES:
            found.append((section, heading_type, json.loads(text)))
    return found


def _fill_elided(value, key=""):
    """Replace "…" strings with valid stand-ins, so the examples' shortened IDs and times parse."""
    if isinstance(value, dict):
        return {k: _fill_elided(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_elided(v, key) for v in value]
    if isinstance(value, str) and "…" in value:
        if key.endswith("Id"):
            return ELIDED_ID
        if key in ("ts", "simTs", "manualUntil") or key.endswith("At"):
            return ELIDED_TIME
        raise AssertionError(f"no stand-in for the elided value of {key!r}; add one to _fill_elided")
    return value


def _ignored_keys(model: BaseModel, data: dict, path: str = "") -> list[str]:
    """Keys in `data` that the model dropped as unknown fields."""
    fields = {(info.alias or name): name for name, info in type(model).model_fields.items()}
    extra = model.model_extra or {}
    ignored = []
    for key, raw in data.items():
        if key in fields:
            parsed = getattr(model, fields[key])
        elif key in extra:
            parsed = extra[key]
        else:
            ignored.append(path + key)
            continue
        if isinstance(parsed, BaseModel) and isinstance(raw, dict):
            ignored += _ignored_keys(parsed, raw, f"{path}{key}.")
    return ignored


EXAMPLES = _contract_examples()
ENVELOPE_EXAMPLES = [example for section, _, example in EXAMPLES if section == "5"]
MESSAGE_EXAMPLES = [(heading_type, example) for section, heading_type, example in EXAMPLES if section != "5"]


def _example(kind: str) -> dict:
    return _fill_elided(next(example for _, example in MESSAGE_EXAMPLES if example["type"] == kind))


def test_contract_has_an_example_of_every_modelled_message():
    assert ENVELOPE_EXAMPLES, "no envelope example found in contract §5"
    assert {example["type"] for _, example in MESSAGE_EXAMPLES} == set(MODELLED_TYPES)
    assert any("msgId" not in example for _, example in MESSAGE_EXAMPLES), "no last-will example found in §6.4"


@pytest.mark.parametrize("example", ENVELOPE_EXAMPLES)
def test_envelope_example_validates(example):
    data = _fill_elided(example)
    envelope = Envelope.model_validate(data)
    assert _ignored_keys(envelope, data) == []


@pytest.mark.parametrize(
    ("heading_type", "example"),
    MESSAGE_EXAMPLES,
    ids=[f"{example['type']}-{i}" for i, (_, example) in enumerate(MESSAGE_EXAMPLES)],
)
def test_message_example_validates(heading_type, example):
    assert example["type"] == heading_type, "example sits under another message's heading"
    data = _fill_elided(example)
    message = parse_farm_message(data)
    assert message.type == example["type"]
    assert _ignored_keys(message, data) == [], "the example uses fields the model doesn't know"


def test_last_will_and_heartbeat_use_different_models():
    last_will = next(example for _, example in MESSAGE_EXAMPLES if "msgId" not in example)
    assert isinstance(parse_farm_message(last_will), DeviceStatusLastWill)
    assert isinstance(parse_farm_message(_example("DEVICE_STATUS") | {"online": False}), DeviceStatus)


def test_unknown_fields_are_ignored():
    data = _example("ZONE_TELEMETRY")
    data["newField"] = 1
    data["readings"]["newReading"] = 2.5
    assert parse_farm_message(data).readings.ec == data["readings"]["ec"]

    climate = _example("CLIMATE_TELEMETRY")
    climate["equipment"]["newField"] = "anything"
    assert "newField" not in parse_farm_message(climate).equipment.model_extra


def test_optional_fields_from_the_contract_prose():
    sim = parse_farm_message(_example("ZONE_TELEMETRY") | {"simTs": "2026-11-10T06:00:00.000Z"})
    assert sim.sim_ts is not None

    gateway_fields = {"device": "GW1", "bufferedMessages": 12, "cloudLink": "DOWN"}
    gateway = parse_farm_message(_example("DEVICE_STATUS") | gateway_fields)
    assert (gateway.buffered_messages, gateway.cloud_link) == (12, "DOWN")


def test_device_status_fields_a_device_cant_report_may_be_null():
    # A simulated or wired device has no radio, heap or firmware build, and no
    # configVersion before its first DEVICE_CONFIG (contract §1: null, never a fake value).
    unknown = {"fwHash": None, "rssiDbm": None, "configVersion": None, "freeHeapKb": None}
    status = parse_farm_message(_example("DEVICE_STATUS") | unknown)
    assert (status.fw_hash, status.rssi_dbm, status.config_version, status.free_heap_kb) == (None,) * 4


def test_wire_names_round_trip():
    data = _example("ZONE_TELEMETRY")
    dumped = parse_farm_message(data).model_dump(mode="json")
    assert ZoneTelemetry.model_validate(dumped) == ZoneTelemetry.model_validate(data)
    assert set(dumped) >= set(data) - {"simTs"}


DELETE = object()


def _changed(data: dict, path: str, value) -> dict:
    """A copy of an example with one dotted path set to `value`, or removed with DELETE."""
    data = copy.deepcopy(data)
    *parents, last = path.split(".")
    target = data
    for part in parents:
        target = target[part]
    if value is DELETE:
        del target[last]
    else:
        target[last] = value
    return data


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("ZONE_TELEMETRY", "v", 2),
        ("ZONE_TELEMETRY", "v", True),
        ("ZONE_TELEMETRY", "msgId", "not-a-uuid"),
        ("ZONE_TELEMETRY", "ts", "2026-11-09T10:24:00.000+05:30"),
        ("ZONE_TELEMETRY", "ts", "2026-11-09T04:54:00"),
        ("ZONE_TELEMETRY", "site", "SIET PH1"),
        ("ZONE_TELEMETRY", "zone", "A"),
        ("ZONE_TELEMETRY", "seq", -1),
        ("ZONE_TELEMETRY", "readings.ec", "1.62"),
        ("ZONE_TELEMETRY", "readings.ec", DELETE),
        ("ZONE_TELEMETRY", "quality.ec", "GOOD"),
        ("ZONE_TELEMETRY", "mode", "remote"),
        ("ZONE_TELEMETRY", "estop", 0),
        ("ZONE_TELEMETRY", "equipment.Z1-CIRC", "RUNNING"),
        ("CLIMATE_TELEMETRY", "mode", "AUTO"),
        ("CLIMATE_TELEMETRY", "equipment.FAN1", "BROKEN"),
        ("CLIMATE_TELEMETRY", "equipment.CH1.setpointC", "22"),
        ("POWER_STATE", "source", "SOLAR"),
        ("POWER_STATE", "generator", "BROKEN"),
        ("POWER_STATE", "ups.onBattery", "false"),
        ("DEVICE_STATUS", "uptimeS", 1.5),
        ("DEVICE_STATUS", "rssiDbm", "-61"),
        ("DEVICE_STATUS", "fwHash", DELETE),
        ("DEVICE_STATUS", "cloudLink", "OFFLINE"),
        ("ZONE_STATE", "operation.kind", "MIX"),
        ("ZONE_STATE", "stopped", DELETE),
        # basic physical limits (the example's quality is OK or not given)
        ("ZONE_TELEMETRY", "readings.ec", -0.01),
        ("ZONE_TELEMETRY", "readings.ph", -0.1),
        ("ZONE_TELEMETRY", "readings.ph", 14.01),
        ("ZONE_TELEMETRY", "readings.reservoirPct", -1),
        ("ZONE_TELEMETRY", "readings.reservoirPct", 100.5),
        ("ZONE_TELEMETRY", "readings.dayTankPct.A", 101),
        ("ZONE_TELEMETRY", "readings.reservoirL", -1),
        ("ZONE_TELEMETRY", "readings.dayTankL.B", -0.5),
        ("ZONE_TELEMETRY", "readings.circFlowLpm", -0.1),
        ("CLIMATE_TELEMETRY", "readings.airRh", 101),
        ("CLIMATE_TELEMETRY", "readings.outsideRh", -1),
        ("CLIMATE_TELEMETRY", "setpoints.airRhMax", 120),
        ("POWER_STATE", "ups.batteryPct", 100.1),
        # a null reading can't have quality OK
        ("ZONE_TELEMETRY", "readings.ec", None),
        ("CLIMATE_TELEMETRY", "readings.airRh", None),
    ],
    ids=lambda value: "removed" if value is DELETE else None,
)
def test_contract_violations_are_rejected(kind, path, value):
    with pytest.raises(ValidationError):
        parse_farm_message(_changed(_example(kind), path, value))


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("ZONE_TELEMETRY", "readings.ec", 0),
        ("ZONE_TELEMETRY", "readings.ph", 0),
        ("ZONE_TELEMETRY", "readings.ph", 14),
        ("ZONE_TELEMETRY", "readings.reservoirPct", 0),
        ("ZONE_TELEMETRY", "readings.reservoirPct", 100),
        ("ZONE_TELEMETRY", "readings.reservoirL", 0),
        ("ZONE_TELEMETRY", "readings.circFlowLpm", 0),
        ("CLIMATE_TELEMETRY", "readings.airRh", 100),
        ("POWER_STATE", "ups.batteryPct", 0),
    ],
)
def test_physical_limits_are_inclusive(kind, path, value):
    parse_farm_message(_changed(_example(kind), path, value))


def test_quality_ok_for_a_map_reading_covers_every_entry():
    data = _changed(_example("ZONE_TELEMETRY"), "readings.dayTankPct.ACID", None)
    parse_farm_message(data)  # no quality for dayTankPct in the example
    with pytest.raises(ValidationError, match="dayTankPct"):
        parse_farm_message(_changed(data, "quality.dayTankPct", "OK"))


@pytest.mark.parametrize(
    "quality",
    ["STALE", "OUT_OF_RANGE", "FAULT", "UNCALIBRATED", DELETE],
    ids=lambda value: "removed" if value is DELETE else None,
)
def test_null_reading_with_a_quality_other_than_ok(quality):
    data = _changed(_example("ZONE_TELEMETRY"), "readings.ec", None)
    message = parse_farm_message(_changed(data, "quality.ec", quality))
    assert message.readings.ec is None


def test_quality_for_a_reading_the_model_doesnt_know_is_ignored():
    data = _changed(_example("ZONE_TELEMETRY"), "quality.newReading", "OK")
    assert parse_farm_message(data).quality["newReading"] == "OK"


# §6.1: physical limits apply only when quality is OK, STALE or UNCALIBRATED (or not given).
# OUT_OF_RANGE carries the raw value or null; FAULT is null.
OUTSIDE_LIMITS = [("ec", -0.2), ("ph", 14.5), ("reservoirPct", 101), ("circFlowLpm", -1)]


@pytest.mark.parametrize(
    "quality",
    ["OK", "STALE", "UNCALIBRATED", DELETE],
    ids=lambda value: "not-given" if value is DELETE else None,
)
@pytest.mark.parametrize(("reading", "value"), OUTSIDE_LIMITS)
def test_physical_limits_apply_unless_quality_is_out_of_range(reading, value, quality):
    data = _changed(_example("ZONE_TELEMETRY"), f"readings.{reading}", value)
    with pytest.raises(ValidationError, match=f"{reading} must be (between|>=)"):
        parse_farm_message(_changed(data, f"quality.{reading}", quality))


@pytest.mark.parametrize(("reading", "value"), [*OUTSIDE_LIMITS, ("ec", None)])
def test_out_of_range_carries_the_raw_value_or_null(reading, value):
    data = _changed(_example("ZONE_TELEMETRY"), f"readings.{reading}", value)
    message = parse_farm_message(_changed(data, f"quality.{reading}", "OUT_OF_RANGE"))
    assert message.readings.model_dump()[reading] == value


@pytest.mark.parametrize("value", [6.04, 14.5])
def test_fault_reading_must_be_null(value):
    data = _changed(_example("ZONE_TELEMETRY"), "quality.ph", "FAULT")
    with pytest.raises(ValidationError, match="ph must be null"):
        parse_farm_message(_changed(data, "readings.ph", value))
    assert parse_farm_message(_changed(data, "readings.ph", None)).readings.ph is None


def test_quality_of_a_map_reading_covers_every_entry_for_limits_and_fault():
    data = _changed(_example("ZONE_TELEMETRY"), "readings.dayTankPct.A", 101)
    with pytest.raises(ValidationError, match="dayTankPct must be between"):
        parse_farm_message(data)  # no quality for dayTankPct in the example
    raw = parse_farm_message(_changed(data, "quality.dayTankPct", "OUT_OF_RANGE"))
    assert raw.readings.day_tank_pct["A"] == 101
    with pytest.raises(ValidationError, match="dayTankPct must be null"):
        parse_farm_message(_changed(data, "quality.dayTankPct", "FAULT"))


def test_climate_readings_follow_the_same_quality_rule():
    data = _changed(_example("CLIMATE_TELEMETRY"), "readings.airRh", 101)
    with pytest.raises(ValidationError, match="airRh must be between"):
        parse_farm_message(data)  # quality OK in the example
    assert parse_farm_message(_changed(data, "quality.airRh", "OUT_OF_RANGE")).readings.air_rh == 101
    with pytest.raises(ValidationError, match="airRh must be null"):
        parse_farm_message(_changed(data, "quality.airRh", "FAULT"))


@pytest.mark.parametrize("left_out", [("generator",), ("ups",), ("generator", "ups")], ids="+".join)
def test_power_state_generator_and_ups_may_be_left_out(left_out):
    # §6.3: left out when the site's layout.json has "generator": false or "ups": false.
    data = _example("POWER_STATE")
    for field in left_out:
        del data[field]
    state = parse_farm_message(data)
    assert all(getattr(state, field) is None for field in left_out)


def test_zone_state_config_version_may_be_null():
    # As in DEVICE_STATUS: no configVersion before the controller's first DEVICE_CONFIG.
    state = parse_farm_message(_example("ZONE_STATE") | {"configVersion": None})
    assert state.config_version is None


def test_last_will_must_say_offline():
    with pytest.raises(ValidationError):
        parse_farm_message({"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": True})


def test_unmodelled_type_is_refused():
    with pytest.raises(ValueError, match="no contract model"):
        parse_farm_message({"v": 1, "type": "SOMETHING_NEW"})
