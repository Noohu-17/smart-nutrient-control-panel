"""The stuck-command guard (T1-23, contract §8.1, §8.4, §14).

A sent command left without replies is marked by the worker, and GET /api/commands/{cmdId} then
answers UNKNOWN with reason TIMED_OUT; a newer CMD_STATE from the farm undoes it. mark_stuck takes
the time, so these tests move the clock on instead of waiting.
"""

import asyncio
import dataclasses
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.exc import OperationalError
from test_commands import ORG, SITE, FakePublisher, client_for, cmd_state, handle
from test_read_api import CONTRACT, write_pack
from test_worker import TOPIC, FakeMessage, run_with_plan, zone_telemetry

import app.worker.main as worker_main
from app.config import load_settings
from app.db.models import SentCommand
from app.db.seed import seed
from app.main import app
from app.worker import stuck
from app.worker.main import Worker
from app.worker.stuck import STUCK_AFTER_S, SWEEP_S, guard, mark_stuck
from conftest import run_alembic

MARGIN = timedelta(seconds=STUCK_AFTER_S)


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pack(tmp_path):
    return write_pack(tmp_path / ORG)


@pytest.fixture
def seeded(migrated_db, pack):
    seed(migrated_db, pack)
    return migrated_db


@pytest.fixture
def worker(seeded):
    return Worker(seeded, ORG, SITE)


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
def api(seeded, pack, publisher):
    return client_for(pack, seeded, publisher)


@pytest.fixture
def worker_settings(tmp_path):
    """Settings for a config pack holding only what the worker's run loop reads from it."""
    client_dir = tmp_path / ORG
    client_dir.mkdir()
    (client_dir / "layout.json").write_text(json.dumps({"site": SITE}), encoding="utf-8")
    return dataclasses.replace(load_settings(), client_dir=client_dir)


def now():
    return datetime.now(UTC)


def sent(engine, cmd_id=None, *, kind="DOSE", expired_s=600, dispatched=True, site=SITE):
    """A command the API sent, as sent_command keeps it, that expired `expired_s` seconds ago."""
    cmd_id = cmd_id or str(uuid.uuid4())
    expires_at = now() - timedelta(seconds=expired_s)
    with engine.begin() as connection:
        connection.execute(insert(SentCommand).values(
            org_id=ORG, site_id=site, cmd_id=cmd_id, kind=kind, topic=f"farm/{site}/zone/Z1/cmd",
            body={"kind": kind}, issued_at=expires_at - timedelta(seconds=60), expires_at=expires_at,
            dispatched_at=expires_at - timedelta(seconds=59) if dispatched else None,
        ))
    return cmd_id


def row(engine, cmd_id, site=SITE):
    with engine.connect() as connection:
        return connection.execute(
            select(SentCommand.last_report_at, SentCommand.timed_out_at).where(
                SentCommand.org_id == ORG, SentCommand.site_id == site, SentCommand.cmd_id == cmd_id
            )
        ).one()


def report(worker, cmd_id, state="EXECUTING", seq=10, **changes):
    handle(worker, cmd_state(state, seq, cmdId=cmd_id, **changes))


def answer(api, cmd_id):
    response = api.get(f"/api/commands/{cmd_id}")
    assert response.status_code == 200, response.text
    return response.json()


def marks(engine, at):
    return mark_stuck(engine, ORG, SITE, at)


# --- which commands are stuck --------------------------------------------------------------


def test_a_stop_left_without_replies_is_unknown_timed_out(api, seeded, publisher):
    cmd_id = api.post("/api/stop", json={"scope": "ZONE", "zone": "Z1"}).json()["cmdId"]
    expires_at = datetime.fromisoformat(publisher.sent[0][1]["expiresAt"])

    assert marks(seeded, expires_at + MARGIN - timedelta(seconds=1)) == []  # still in its margin
    assert answer(api, cmd_id)["state"] == "DISPATCHED"
    assert marks(seeded, expires_at + MARGIN) == [(cmd_id, "STOP")]
    assert answer(api, cmd_id) == {
        "cmdId": cmd_id, "device": None, "state": "UNKNOWN", "reason": "TIMED_OUT", "lines": None,
        "before": None, "after": None, "startedAt": None, "endedAt": None,
    }


def test_nothing_is_stuck_before_it_expires(seeded):
    sent(seeded, expired_s=-240)  # a stop, four minutes before its expiry
    assert marks(seeded, now() + timedelta(seconds=200)) == []


def test_a_running_dose_that_falls_silent_is_marked_with_what_it_had_delivered(api, seeded, worker):
    cmd_id = sent(seeded)
    report(worker, cmd_id, "EXECUTING", 10)
    heard_at = row(seeded, cmd_id).last_report_at

    assert marks(seeded, heard_at + MARGIN - timedelta(seconds=1)) == []  # it reported just now
    assert marks(seeded, heard_at + MARGIN) == [(cmd_id, "DOSE")]
    shown = answer(api, cmd_id)
    assert (shown["state"], shown["reason"], shown["device"]) == ("UNKNOWN", "TIMED_OUT", "ZC1")
    assert [(line["line"], line["deliveredMl"]) for line in shown["lines"]] == [("A", 64), ("B", 0)]
    assert shown["before"] == {"ec": 1.51, "ph": 6.21}


@pytest.mark.parametrize("final", ["COMPLETED", "PARTIAL", "FAILED", "STOPPED", "REJECTED"])
def test_a_command_with_a_final_state_is_never_marked(api, seeded, worker, final):
    cmd_id = sent(seeded)
    report(worker, cmd_id, final, 11, reason=None if final == "COMPLETED" else "NO_FLOW")
    assert marks(seeded, now() + timedelta(days=2)) == []
    assert answer(api, cmd_id)["state"] == final


@pytest.mark.parametrize("state", ["ACCEPTED", "EXECUTING"])
def test_a_command_still_running_by_its_last_report_can_be_marked(seeded, worker, state):
    cmd_id = sent(seeded)
    report(worker, cmd_id, state, 10)
    assert marks(seeded, now() + MARGIN + timedelta(seconds=1)) == [(cmd_id, "DOSE")]


def test_a_command_the_broker_never_took_is_never_marked(seeded):
    sent(seeded, dispatched=False)
    assert marks(seeded, now() + timedelta(days=2)) == []


def test_a_command_is_marked_once(seeded):
    cmd_id = sent(seeded)
    first = now()
    assert marks(seeded, first) == [(cmd_id, "DOSE")]
    assert marks(seeded, first + timedelta(minutes=5)) == []
    assert row(seeded, cmd_id).timed_out_at == first  # the first time stands


def test_another_sites_mark_never_shows_here(api, seeded, worker):
    cmd_id = str(uuid.uuid4())
    sent(seeded, cmd_id, site="another-site")  # the same cmdId on another site, written first
    mark_stuck(seeded, ORG, "another-site", now())
    sent(seeded, cmd_id)
    report(worker, cmd_id, "EXECUTING", 10)
    assert answer(api, cmd_id)["state"] == "EXECUTING"


def test_openapi_lists_unknown_among_the_answers_states(api):
    schema = api.get("/api/openapi.json").json()["components"]["schemas"]["CommandStatus"]["properties"]["state"]
    states = {value for option in schema["anyOf"] for value in option.get("enum", [option.get("const")])}
    assert {"DISPATCHED", "UNKNOWN", "EXECUTING", "COMPLETED"} <= states


def test_another_sites_commands_are_left_alone(seeded):
    sent(seeded, site="another-site")
    assert marks(seeded, now()) == []
    assert mark_stuck(seeded, ORG, "another-site", now()) != []  # its own worker marks it


# --- the farm's word wins ------------------------------------------------------------------


def test_a_newer_report_clears_the_mark_and_shows_the_farms_state(api, seeded, worker):
    cmd_id = sent(seeded)
    report(worker, cmd_id, "EXECUTING", 10)
    marks(seeded, now() + MARGIN + timedelta(seconds=1))
    assert answer(api, cmd_id)["state"] == "UNKNOWN"

    report(worker, cmd_id, "EXECUTING", 11)  # the farm is back, still dosing
    assert row(seeded, cmd_id).timed_out_at is None
    shown = answer(api, cmd_id)
    assert (shown["state"], shown["reason"]) == ("EXECUTING", None)

    report(worker, cmd_id, "COMPLETED", 12)
    assert answer(api, cmd_id)["state"] == "COMPLETED"
    assert marks(seeded, now() + timedelta(days=2)) == []


def test_a_replayed_older_report_does_not_clear_the_mark(api, seeded, worker):
    cmd_id = sent(seeded)
    report(worker, cmd_id, "EXECUTING", 10)
    marks(seeded, now() + MARGIN + timedelta(seconds=1))
    report(worker, cmd_id, "ACCEPTED", 9)  # older: a replay after a link drop
    assert row(seeded, cmd_id).timed_out_at is not None
    assert answer(api, cmd_id)["state"] == "UNKNOWN"


def test_a_final_report_always_shows_even_if_marked_at_the_same_moment(api, seeded, worker):
    cmd_id = sent(seeded)
    report(worker, cmd_id, "COMPLETED", 12)
    with seeded.begin() as connection:  # the guard and the report racing: the report won
        connection.execute(update(SentCommand).where(SentCommand.cmd_id == cmd_id).values(timed_out_at=now()))
    assert answer(api, cmd_id)["state"] == "COMPLETED"


def test_every_newer_report_is_noted_in_server_time(seeded, worker):
    cmd_id = sent(seeded)
    before = now()
    report(worker, cmd_id, "ACCEPTED", 10, ts="2020-01-01T00:00:00.000Z")  # a device clock far off
    heard_at = row(seeded, cmd_id).last_report_at
    assert before <= heard_at <= now()


def test_a_report_for_a_command_this_api_never_sent_changes_nothing_else(api, seeded, worker):
    report(worker, str(uuid.uuid4()), "EXECUTING", 10)  # stored as before; nothing to mark
    assert marks(seeded, now() + timedelta(days=2)) == []


# --- the guard in the worker ---------------------------------------------------------------


def run_guard(engine, seconds, monkeypatch, sweep_s=0.02):
    monkeypatch.setattr(stuck, "SWEEP_S", sweep_s)

    async def for_a_while():
        task = asyncio.create_task(guard(engine, ORG, SITE))
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return task

    return asyncio.run(for_a_while())


def test_the_guard_marks_stuck_commands_and_says_so(seeded, monkeypatch, caplog):
    cmd_id = sent(seeded, kind="RESUME")
    with caplog.at_level(logging.WARNING, logger="worker"):
        task = run_guard(seeded, 0.5, monkeypatch)
    assert task.cancelled()
    assert row(seeded, cmd_id).timed_out_at is not None
    expected = (
        f"RESUME {cmd_id}: no reply from the farm for {STUCK_AFTER_S} s after it expired or last "
        "reported; now UNKNOWN, TIMED_OUT"
    )
    assert [m for m in caplog.messages if cmd_id in m] == [expected]


def test_the_guard_waits_before_its_first_look(seeded, monkeypatch):
    sent(seeded)
    run_guard(seeded, 0.1, monkeypatch, sweep_s=5)
    assert marks(seeded, now()) != []  # still unmarked: the guard hadn't looked yet


def test_the_guard_keeps_going_while_the_database_cant_answer(monkeypatch, caplog):
    def down(*_args):
        raise OperationalError("UPDATE sent_command", {}, Exception("connection refused"))

    monkeypatch.setattr(stuck, "mark_stuck", down)
    with caplog.at_level(logging.WARNING, logger="worker"):
        task = run_guard(None, 0.3, monkeypatch)
    assert task.cancelled()
    assert sum(m.startswith("stuck-command guard: database not ready (connection refused)")
               for m in caplog.messages) >= 2


def test_the_guard_keeps_going_after_an_unexpected_error(monkeypatch, caplog):
    calls = []

    def broken(*args):
        calls.append(args)
        raise RuntimeError("boom")

    monkeypatch.setattr(stuck, "mark_stuck", broken)
    with caplog.at_level(logging.ERROR, logger="worker"):
        task = run_guard(None, 0.3, monkeypatch)
    assert task.cancelled() and len(calls) >= 2
    assert "stuck-command guard failed" in caplog.text


def test_the_worker_runs_the_guard_and_stops_it_with_itself(worker_settings, migrated_db, monkeypatch):
    seen = []

    async def fake_guard(engine, org_id, site_id):
        seen.append(("started", org_id, site_id))
        try:
            await asyncio.sleep(5)  # cancelled long before, unless the worker forgets to
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise

    monkeypatch.setattr(worker_main, "guard", fake_guard)
    settings = dataclasses.replace(worker_settings, db_name=migrated_db.url.database)
    message = FakeMessage(TOPIC, json.dumps(zone_telemetry()).encode())  # storing it lets the guard start
    run_with_plan(settings, [[message], "stop"])
    assert seen == [("started", worker_settings.client_dir.name, SITE), "cancelled"]


# --- the migration and the contract --------------------------------------------------------


def test_migration_0005_adds_the_two_columns_and_takes_them_away(migrated_db):
    def columns():
        return {c["name"] for c in inspect(migrated_db).get_columns("sent_command")}

    assert {"last_report_at", "timed_out_at"} <= columns()
    run_alembic(migrated_db, "downgrade", "0004")
    assert not {"last_report_at", "timed_out_at"} & columns()
    run_alembic(migrated_db, "upgrade", "head")
    assert {"last_report_at", "timed_out_at"} <= columns()


def test_the_contract_gives_the_guards_reason_and_timing():
    text = CONTRACT.read_text(encoding="utf-8")
    assert re.search(r"^\| `TIMED_OUT` \| cloud \|", text, re.MULTILINE)
    timing = next(line for line in text.splitlines() if line.startswith("| No reply") and "TIMED_OUT" in line)
    assert f"{STUCK_AFTER_S} s" in timing and f"every {SWEEP_S} s" in timing
