"""The EVENT model (contract §6.5, T1-17), and the worker taking events as messages from a device."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_read_api import write_pack

from app.contract import Event, parse_farm_message
from app.contract.farm_messages import EVENT_SEVERITY, EventKind
from app.db.models import Device, Reading
from app.db.seed import seed
from app.worker.main import Worker

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contract.md"
ORG, SITE = "client_A", "siet-ph1"


def section_6_5() -> str:
    return CONTRACT.read_text(encoding="utf-8").partition("### 6.5 ")[2].partition("\n### ")[0]


def contract_example() -> dict:
    example = json.loads(re.search(r"```json\n(.*?)```", section_6_5(), re.DOTALL).group(1))
    return example | {"msgId": "00000000-0000-4000-8000-000000000000", "ts": "2026-11-09T04:54:00.000Z"}


def event(**changes):
    """An E-STOP event as the simulator's zone controller sends it (T4-8)."""
    return {
        "v": 1, "type": "EVENT", "msgId": "0b7f2c1e-5b8e-4a59-9c4e-0c8d3a7f1a21",
        "ts": "2026-11-09T04:55:02.000Z", "site": SITE, "device": "ZC1", "bootId": 4, "seq": 10,
        "zone": "Z1", "kind": "ESTOP_PRESSED", "severity": "CRITICAL",
        "message": "E-STOP pressed on the Zone 1 dosing cabinet",
        "systemAction": "All dosing and refill in Zone 1 stopped; circulation keeps running",
        "nextStep": "Check the cabinet, release the E-STOP, then press Resume in the app",
        "detail": {"source": "ZC1 panel"},
    } | changes


# --- The model, against the contract --------------------------------------------------


def test_the_contract_example_is_an_event_and_every_field_is_kept():
    example = contract_example()
    parsed = parse_farm_message(example)
    assert isinstance(parsed, Event)
    dumped = parsed.model_dump(mode="json")
    assert set(example) <= set(dumped)  # nothing in the example is dropped as unknown
    for key in ("zone", "kind", "severity", "message", "systemAction", "nextStep", "detail"):
        assert dumped[key] == example[key], key


def table_rows() -> list[tuple[list[str], list[str]]]:
    """(kinds, severities) for each row of the §6.5 table."""
    rows = []
    for line in section_6_5().splitlines():
        if not line.startswith("| `") or line.startswith("| `kind`"):
            continue
        kinds, severity = (cell.strip() for cell in line.strip("|").split("|"))
        rows.append((re.findall(r"`([A-Z_]+)`", kinds), severity.split(" / ")))
    return rows


def test_the_kinds_are_the_ones_in_the_contract():
    in_the_table = {kind for kinds, _ in table_rows() for kind in kinds}
    assert set(get_args(EventKind)) == in_the_table
    assert set(EVENT_SEVERITY) == in_the_table - {"MODE_CHANGED"}


def test_each_severity_is_the_one_in_the_contract():
    for kinds, severities in table_rows():
        if kinds == ["MODE_CHANGED"]:
            assert severities == ["WARNING if leaving REMOTE, otherwise INFO"]
            continue
        if len(severities) == 1:
            severities = severities * len(kinds)
        assert {kind: EVENT_SEVERITY[kind] for kind in kinds} == dict(zip(kinds, severities, strict=True))


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"kind": "ESTOP_RELEASED", "severity": "INFO"},
        {"kind": "MODE_CHANGED", "severity": "WARNING", "detail": {"from": "REMOTE", "to": "LOCAL"}},
        {"kind": "MODE_CHANGED", "severity": "INFO", "detail": {"from": "LOCAL", "to": "REMOTE"}},
        {"kind": "MODE_CHANGED", "severity": "INFO", "detail": {"from": "LOCAL", "to": "MAINT"}},
        {"kind": "REBOOTED", "severity": "WARNING", "detail": {"reason": "POWER_CUT"}},
    ],
    ids=["estop-pressed", "estop-released", "leaving-remote", "back-to-remote", "local-to-maint", "rebooted"],
)
def test_the_events_the_simulator_sends_pass(changes):
    assert isinstance(parse_farm_message(event(**changes)), Event)


def test_a_climate_event_names_no_zone():
    parsed = parse_farm_message(event(device="CC1", zone=None, kind="CLIMATE_MODE_REVERTED", severity="INFO"))
    assert parsed.zone is None
    message = event(device="CC1", kind="CHILLER_FAULT", detail={"equipment": "CH1"})
    del message["zone"]
    assert parse_farm_message(message).zone is None


@pytest.mark.parametrize(
    ("changes", "complaint"),
    [
        ({"severity": "WARNING"}, "ESTOP_PRESSED event is CRITICAL"),
        ({"kind": "MODE_CHANGED", "severity": "INFO", "detail": {"from": "REMOTE", "to": "LOCAL"}}, "is WARNING"),
        ({"kind": "MODE_CHANGED", "severity": "WARNING", "detail": None}, "is INFO"),
        ({"kind": "DOOR_OPENED"}, "kind"),
        ({"severity": "LOW"}, "severity"),
        ({"message": ""}, "message"),
        ({"nextStep": None}, "nextStep"),
        ({"zone": "zone 1"}, "zone"),
    ],
    ids=["severity", "mode-leaving-remote", "mode-no-detail", "unknown-kind", "unknown-severity",
         "empty-message", "no-next-step", "bad-zone"],
)
def test_an_event_that_breaks_the_contract_is_refused(changes, complaint):
    with pytest.raises(ValidationError, match=complaint):
        parse_farm_message(event(**changes))


def test_an_event_without_one_of_its_three_answers_is_refused():
    for field in ("message", "systemAction", "nextStep"):
        message = event()
        del message[field]
        with pytest.raises(ValidationError, match=field):
            parse_farm_message(message)


# --- The worker -----------------------------------------------------------------------


@pytest.fixture
def worker(migrated_db, tmp_path):
    seed(migrated_db, write_pack(tmp_path / ORG))
    return Worker(migrated_db, ORG, SITE)


def test_the_worker_takes_an_event_as_a_message_from_its_device(worker, caplog):
    caplog.set_level(logging.DEBUG, logger="worker")
    asyncio.run(worker.handle(f"farm/{SITE}/zone/Z1/event", json.dumps(event()).encode()))
    with worker.engine.connect() as connection:
        last_seen = connection.scalar(select(Device.last_seen).where(Device.device_id == "ZC1"))
        readings = connection.execute(select(Reading)).all()
    assert last_seen is not None  # heard from: it counts toward online (§14)
    assert readings == []  # an event has no readings
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert not any("no model yet" in m for m in caplog.messages)
