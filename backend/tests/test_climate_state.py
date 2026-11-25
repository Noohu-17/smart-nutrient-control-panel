"""The CLIMATE_STATE model (T1-14) against contract §6.9.

§6.9 gives this message in words, not as JSON: `mode`, `manualUntil`, `stopped`, `equipment`
(as in 6.2) and `latches`. So the example here comes from the contract itself: its field list
is read from §6.9, and the shared fields from the §6.2 example. Any JSON example §6.9 gains
later is checked as well, with no change here.
"""

import asyncio
import json
import logging
import re

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_contract_models import CONTRACT, DELETE, _changed, _example, _fill_elided, _ignored_keys

from app.contract import ClimateState, Envelope, parse_farm_message
from app.contract.watch import check
from app.db.models import Reading
from app.worker.main import Worker
from app.worker.store import reading_rows

ORG = "client_A"
TOPIC = "farm/siet-ph1/climate/state"

# As the simulator sends it (simulator/sim/messages.py, T4-6), from a real run: everything off
# in MANUAL, with no revert time, until the first command.
SIMULATOR_STATE = {
    "v": 1, "type": "CLIMATE_STATE", "msgId": "8183c201-efed-4b8f-a7f6-6e2bda46200c",
    "ts": "2026-11-02T09:40:48.182Z", "site": "siet-ph1", "device": "CC1", "bootId": 2, "seq": 1,
    "simTs": "2026-11-02T09:40:48.160Z",
    "mode": "MANUAL", "manualUntil": None, "stopped": False,
    "equipment": {
        "fanStage": 0, "FAN1": "OFF", "FAN2": "OFF", "FAN3": "OFF", "FAN4": "OFF", "PAD1": "OFF",
        "SHADE1": "OPEN", "HAF1": "OFF", "FOG1": "NOT_INSTALLED",
        "CH1": {"state": "OFF", "setpointC": 22.0}, "CH2": {"state": "OFF", "setpointC": 22.0},
    },
    "latches": [],
}


def section_6_9() -> str:
    text = CONTRACT.read_text(encoding="utf-8")
    return re.search(r"^### 6\.9 .*?(?=^##)", text, re.MULTILINE | re.DOTALL).group()


def example() -> dict:
    """The message §6.9 describes: the §6.2 example's envelope and shared fields, as it says."""
    telemetry = _example("CLIMATE_TELEMETRY")
    envelope = ("v", "msgId", "ts", "site", "device", "bootId", "seq")
    shared = ("mode", "manualUntil", "equipment")
    return {key: telemetry[key] for key in envelope + shared} | {
        "type": "CLIMATE_STATE", "stopped": False, "latches": [],
    }


def contract_examples() -> list[dict]:
    """That message, and any JSON example §6.9 gains later."""
    blocks = re.findall(r"^```json\n(.*?)^```", section_6_9(), re.MULTILINE | re.DOTALL)
    return [example()] + [_fill_elided(json.loads(block)) for block in blocks]


def payload(message: dict) -> bytes:
    return json.dumps(message).encode()


# --- The model against the contract -----------------------------------------------------


def test_the_model_has_exactly_the_fields_section_6_9_names():
    words = section_6_9().split("\n\n")[1]  # the paragraph under the heading
    named = set(re.findall(r"`([a-z]\w*)`", words))
    own_fields = {
        info.alias
        for name, info in ClimateState.model_fields.items()
        if name not in Envelope.model_fields
    }
    assert named == own_fields


@pytest.mark.parametrize("data", contract_examples())
def test_the_examples_from_the_contract_validate(data):
    message = parse_farm_message(data)
    assert isinstance(message, ClimateState)
    assert _ignored_keys(message, data) == [], "the example uses fields the model doesn't know"


def test_what_the_simulator_sends_validates():
    message = parse_farm_message(SIMULATOR_STATE)
    assert isinstance(message, ClimateState)
    assert _ignored_keys(message, SIMULATOR_STATE) == []
    assert (message.mode, message.manual_until, message.stopped, message.latches) == (
        "MANUAL", None, False, [],
    )
    assert message.equipment.fan_stage == 0
    assert message.equipment.model_extra["FOG1"] == "NOT_INSTALLED"
    assert message.sim_ts is not None


def test_equipment_is_read_as_in_section_6_2():
    climate_state = parse_farm_message(example())
    telemetry = parse_farm_message(_example("CLIMATE_TELEMETRY"))
    assert climate_state.equipment == telemetry.equipment


def test_a_manual_revert_time_and_latches_are_read():
    data = example() | {
        "mode": "MANUAL", "manualUntil": "2026-11-09T05:54:00.000Z", "stopped": True,
        "latches": ["HIGH_TEMP", "FAN_FAULT"],
    }
    message = parse_farm_message(data)
    assert message.manual_until.isoformat() == "2026-11-09T05:54:00+00:00"
    assert message.stopped is True
    assert message.latches == ["HIGH_TEMP", "FAN_FAULT"]


def test_equipment_ids_come_from_the_layout_not_the_model():
    equipment = {
        "fanStage": 6, "FAN6": "ON", "PAD2": "OFF", "CH3": {"state": "ON", "setpointC": 20.5},
    }
    message = parse_farm_message(example() | {"equipment": equipment})
    assert set(message.equipment.model_extra) == {"FAN6", "PAD2", "CH3"}


def test_unknown_fields_are_ignored():
    data = example() | {"newField": 1}
    data["equipment"] = data["equipment"] | {"newField": "anything"}
    message = parse_farm_message(data)
    assert not hasattr(message, "newField")
    assert "newField" not in message.equipment.model_extra


def test_wire_names_round_trip():
    data = example()
    dumped = parse_farm_message(data).model_dump(mode="json")
    assert ClimateState.model_validate(dumped) == ClimateState.model_validate(data)
    assert set(dumped) >= set(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("v", 2),
        ("msgId", "not-a-uuid"),
        ("msgId", DELETE),
        ("ts", "2026-11-09T10:24:00.000+05:30"),
        ("site", "SIET PH1"),
        ("seq", -1),
        ("mode", "AUTO"),
        ("mode", "REMOTE"),  # a zone's mode, not a climate one
        ("mode", DELETE),
        ("manualUntil", "2026-11-09T10:24:00.000+05:30"),
        ("manualUntil", "soon"),
        ("manualUntil", DELETE),
        ("stopped", "false"),
        ("stopped", 0),
        ("stopped", None),
        ("stopped", DELETE),
        ("equipment", DELETE),
        ("equipment.fanStage", DELETE),
        ("equipment.fanStage", -1),
        ("equipment.fanStage", 1.5),
        ("equipment.FAN1", "BROKEN"),
        ("equipment.CH1.state", "RUNNING"),
        ("equipment.CH1.setpointC", "22"),
        ("latches", "LEAK"),
        ("latches", [1]),
        ("latches", None),
        ("latches", DELETE),
    ],
    ids=lambda value: "removed" if value is DELETE else None,
)
def test_contract_violations_are_rejected(path, value):
    with pytest.raises(ValidationError):
        parse_farm_message(_changed(example(), path, value))


def test_another_type_is_rejected():
    with pytest.raises(ValidationError):
        ClimateState.model_validate(example() | {"type": "ZONE_STATE"})


# --- The watch tool --------------------------------------------------------------------


def test_the_watch_tool_shows_it_as_ok():
    assert check(payload(SIMULATOR_STATE)) == ("OK", "CLIMATE_STATE from CC1")


def test_the_watch_tool_names_the_field_that_breaks_the_contract():
    result, detail = check(payload(SIMULATOR_STATE | {"stopped": "false"}))
    assert result == "FAIL"
    assert "stopped" in detail


# --- The worker: nothing changes for what it stores --------------------------------------


def test_it_has_no_values_for_the_reading_table():
    assert reading_rows(parse_farm_message(example()), ORG) == []


def handle(worker, message):
    asyncio.run(worker.handle(TOPIC, payload(message)))


def stored_readings(engine):
    with engine.connect() as connection:
        return connection.execute(select(Reading.metric)).all()


def test_the_worker_accepts_it_and_stores_nothing(migrated_db, caplog):
    worker = Worker(migrated_db, ORG, SIMULATOR_STATE["site"])
    handle(worker, SIMULATOR_STATE)
    assert stored_readings(migrated_db) == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_worker_skips_a_bad_one_and_carries_on(migrated_db, caplog):
    worker = Worker(migrated_db, ORG, SIMULATOR_STATE["site"])
    handle(worker, SIMULATOR_STATE | {"stopped": "false"})
    assert stored_readings(migrated_db) == []
    assert any("skipping a bad message" in m and "stopped" in m for m in caplog.messages)

    telemetry = _example("CLIMATE_TELEMETRY")
    handle(worker, telemetry)
    assert stored_readings(migrated_db) != []
