"""The latest zone and climate state, and the last will (T1-15).

The worker keeps each device's newest ZONE_STATE, CLIMATE_STATE and ZONE_TELEMETRY, and its
last_seen and last will; GET /api/zones and /api/devices answer from them (contract §11.2).
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert as plain_insert
from sqlalchemy import inspect, select, text
from test_read_api import LAYOUT, ago, api_client, write_pack
from test_worker import climate_telemetry, heartbeat, zone_state, zone_telemetry

from app.api.schemas import contract_time
from app.api.status import OFFLINE_AFTER, is_online
from app.contract import parse_farm_message
from app.db.models import Device, LatestMessage, Reading
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker
from app.worker.store import reading_rows
from conftest import run_alembic

ORG = "client_A"
SITE = LAYOUT["site"]
CMD_ID = "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44"
LAST_WILL = {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False}
STATUS = "farm/siet-ph1/device/ZC1/status"
SLOW_CLOCK_S = 30  # how far behind the server's clock a device's runs, in the tests that need it


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
def api(seeded, pack):
    return api_client(pack, seeded)


def handle(worker, message, topic="farm/siet-ph1/zone/Z1/state"):
    asyncio.run(worker.handle(topic, json.dumps(message).encode()))


def now_ts(seconds_ago=1):
    return contract_time(ago(seconds_ago))


def kept(engine, device="ZC1", kind="ZONE_STATE"):
    """The kept message's body, or None."""
    with engine.connect() as connection:
        return connection.scalar(
            select(LatestMessage.body).where(
                LatestMessage.device_id == device, LatestMessage.type == kind
            )
        )


def device_row(engine, device="ZC1"):
    with engine.connect() as connection:
        return connection.execute(
            select(Device.last_seen, Device.last_will_at).where(Device.device_id == device)
        ).one()


# --- What the worker keeps ---------------------------------------------------------------


def test_a_zone_state_is_kept_as_it_was_sent(worker):
    message = zone_state(ts=now_ts(), outputs={"Z1-CIRC": "ON", "Z1-DOSE-A": "OFF"})
    handle(worker, message)
    assert kept(worker.engine) == parse_farm_message(message).model_dump(mode="json")
    with worker.engine.connect() as connection:
        assert connection.scalar(select(LatestMessage.zone_id)) == "Z1"


@pytest.mark.parametrize(
    ("first", "second", "kept_one"),
    [
        ((4, 18), (4, 19), "second"),  # a later message of the same boot
        ((4, 19), (4, 18), "first"),  # an older one that arrives late
        ((4, 18), (4, 18), "first"),  # the same message again
        ((4, 900), (5, 1), "second"),  # after a restart, seq starts again
        ((5, 1), (4, 900), "first"),  # from before the restart, arriving late
    ],
    ids=["newer-seq", "older-seq", "replay", "new-boot", "old-boot"],
)
def test_only_a_newer_message_replaces_the_kept_one(worker, first, second, kept_one):
    a = zone_state(ts=now_ts(), bootId=first[0], seq=first[1], mode="REMOTE")
    b = zone_state(ts=now_ts(), bootId=second[0], seq=second[1], mode="MAINT")
    handle(worker, a)
    handle(worker, b)
    assert kept(worker.engine)["mode"] == ("REMOTE" if kept_one == "first" else "MAINT")


def test_an_older_state_is_logged_as_a_replay(worker, caplog):
    caplog.set_level(logging.INFO, logger="worker")
    handle(worker, zone_state(ts=now_ts(), seq=20))
    handle(worker, zone_state(ts=now_ts(), seq=19))
    assert sum("dropped a replay: ZONE_STATE from ZC1" in m for m in caplog.messages) == 1


def test_one_row_per_device_and_kind(worker):
    for seq in range(10, 15):
        handle(worker, zone_state(ts=now_ts(), seq=seq))
        handle(worker, zone_telemetry(ts=now_ts(), seq=seq + 100), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, climate_telemetry(ts=now_ts()) | {"type": "CLIMATE_STATE"} | climate_state_fields())
    with worker.engine.connect() as connection:
        rows = connection.execute(select(LatestMessage.device_id, LatestMessage.type)).all()
    assert sorted(rows) == [("CC1", "CLIMATE_STATE"), ("ZC1", "ZONE_STATE"), ("ZC1", "ZONE_TELEMETRY")]


def climate_state_fields():
    """A CLIMATE_STATE's own fields (§6.9), on top of an envelope."""
    return {
        "mode": "MANUAL", "manualUntil": None, "stopped": False, "latches": [],
        "equipment": {"fanStage": 0, "FAN1": "OFF"},
    }


def test_a_climate_state_is_kept(worker):
    message = climate_telemetry(ts=now_ts())
    message = {k: message[k] for k in ("v", "msgId", "ts", "site", "device", "bootId", "seq")}
    handle(worker, message | {"type": "CLIMATE_STATE"} | climate_state_fields(), "farm/siet-ph1/climate/state")
    assert kept(worker.engine, "CC1", "CLIMATE_STATE")["mode"] == "MANUAL"
    assert kept(worker.engine, "CC1", "CLIMATE_STATE")["equipment"] == {"fanStage": 0, "FAN1": "OFF"}


def test_zone_telemetry_still_stores_its_readings_and_is_kept_too(worker):
    message = zone_telemetry(ts=now_ts(), leak=True, activeCmdId=CMD_ID)
    handle(worker, message, "farm/siet-ph1/zone/Z1/telemetry")
    with worker.engine.connect() as connection:
        stored = connection.scalar(select(Reading.metric).where(Reading.metric == "ec"))
    assert stored == "ec"
    assert (kept(worker.engine, kind="ZONE_TELEMETRY")["leak"], kept(worker.engine, kind="ZONE_TELEMETRY")["activeCmdId"]) == (True, CMD_ID)


@pytest.mark.parametrize(
    "message",
    [zone_state(), zone_telemetry(), heartbeat()],
    ids=["zone-state", "zone-telemetry", "heartbeat"],
)
def test_every_message_sets_last_seen_and_an_older_one_never_moves_it_back(worker, message):
    new, old = now_ts(5), now_ts(500)
    handle(worker, message | {"ts": new})
    handle(worker, message | {"ts": old, "seq": message["seq"] + 1})
    assert contract_time(device_row(worker.engine).last_seen) == new


def test_the_last_will_marks_the_device_offline(worker):
    handle(worker, heartbeat(ts=now_ts(5)))
    before = datetime.now(UTC)
    handle(worker, LAST_WILL, "farm/siet-ph1/device/ZC1/status")
    seen, will = device_row(worker.engine)
    assert before <= will <= datetime.now(UTC)
    assert not is_online(seen, datetime.now(UTC), will)


def test_an_offline_heartbeat_counts_as_a_last_will(worker):
    handle(worker, heartbeat(ts=now_ts(2), online=False), "farm/siet-ph1/device/ZC1/status")
    seen, will = device_row(worker.engine)
    assert will is not None and not is_online(seen, datetime.now(UTC), will)


@pytest.mark.parametrize(
    ("message", "topic"),
    [
        (heartbeat(), STATUS),
        (zone_telemetry(), "farm/siet-ph1/zone/Z1/telemetry"),
        (zone_state(), "farm/siet-ph1/zone/Z1/state"),
    ],
    ids=["heartbeat", "zone-telemetry", "zone-state"],
)
def test_anything_new_clears_the_last_will_even_from_a_slow_clock(worker, message, topic):
    handle(worker, LAST_WILL, STATUS)
    handle(worker, message | {"ts": now_ts(SLOW_CLOCK_S)}, topic)  # stamped before the will came
    seen, will = device_row(worker.engine)
    assert will is None
    assert is_online(seen, datetime.now(UTC), will)


def test_a_replay_doesnt_clear_the_last_will(worker):
    state, beat = zone_state(ts=now_ts(5), seq=50), heartbeat(ts=now_ts(5), seq=51)
    handle(worker, state)
    handle(worker, beat, STATUS)
    handle(worker, LAST_WILL, STATUS)
    handle(worker, state)  # the same messages again, as after a reconnect
    handle(worker, beat, STATUS)
    handle(worker, zone_state(ts=now_ts(5), seq=49))  # older than the kept one
    seen, will = device_row(worker.engine)
    assert will is not None and not is_online(seen, datetime.now(UTC), will)


def test_another_devices_message_doesnt_clear_the_last_will(worker):
    handle(worker, LAST_WILL, STATUS)
    handle(worker, heartbeat("GW1", ts=now_ts()), "farm/siet-ph1/device/GW1/status")
    assert device_row(worker.engine).last_will_at is not None


def test_a_new_last_will_after_it_came_back_marks_it_offline_again(worker):
    handle(worker, LAST_WILL, STATUS)
    handle(worker, heartbeat(ts=now_ts(SLOW_CLOCK_S)), STATUS)
    handle(worker, LAST_WILL, STATUS)
    seen, will = device_row(worker.engine)
    assert will is not None and not is_online(seen, datetime.now(UTC), will)


def test_a_last_will_from_a_device_not_in_the_layout_is_harmless(worker, caplog):
    handle(worker, LAST_WILL | {"device": "ZC9"}, "farm/siet-ph1/device/ZC9/status")
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_last_will_waits_until_the_database_is_ready(empty_db, pack, caplog):
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            run_alembic(empty_db, "upgrade", "head")
            seed(empty_db, pack)

    worker = Worker(empty_db, ORG, SITE, sleep=sleep)
    handle(worker, LAST_WILL, "farm/siet-ph1/device/ZC1/status")
    assert sleeps == [1, 2]
    assert device_row(empty_db).last_will_at is not None
    assert any("keeping the last will of ZC1" in m for m in caplog.messages)


def test_seeding_again_keeps_last_seen_and_the_last_will(worker, pack):
    handle(worker, heartbeat(ts=now_ts(3)))
    handle(worker, LAST_WILL, "farm/siet-ph1/device/ZC1/status")
    before = device_row(worker.engine)
    seed(worker.engine, pack)
    assert device_row(worker.engine) == before


def test_another_site_keeps_its_own_state(worker, seeded, tmp_path):
    seed(seeded, write_pack(tmp_path / "second" / ORG, LAYOUT | {"site": "another-site"}))
    other = Worker(seeded, ORG, "another-site")
    handle(other, zone_state(ts=now_ts(), site="another-site", mode="MAINT"), "farm/another-site/zone/Z1/state")
    handle(worker, zone_state(ts=now_ts(), mode="REMOTE"))
    with seeded.connect() as connection:
        modes = dict(connection.execute(select(LatestMessage.site_id, LatestMessage.body["mode"].astext)).all())
    assert modes == {SITE: "REMOTE", "another-site": "MAINT"}


# --- The migration -----------------------------------------------------------------------


def test_the_migration_keeps_the_last_seen_of_devices_that_reported_before(empty_db, pack):
    run_alembic(empty_db, "upgrade", "0001")
    with empty_db.begin() as connection:
        connection.execute(text("INSERT INTO site VALUES ('client_A', 'siet-ph1', 'A farm', 'Asia/Kolkata')"))
        connection.execute(text(
            "INSERT INTO device (org_id, site_id, device_id, kind, zone_id) VALUES "
            "('client_A', 'siet-ph1', 'GW1', 'GATEWAY', NULL), ('client_A', 'siet-ph1', 'CC1', 'CLIMATE_CONTROLLER', NULL)"
        ))
        for seconds in (300, 60):
            rows = reading_rows(parse_farm_message(heartbeat("GW1", ts=now_ts(seconds))), ORG)
            connection.execute(plain_insert(Reading.__table__).values(rows))
    run_alembic(empty_db, "upgrade", "head")
    with empty_db.connect() as connection:
        seen = dict(connection.execute(text("SELECT device_id, last_seen FROM device")).all())
    assert seen["CC1"] is None
    assert seen["GW1"] is not None and ago(60) - timedelta(seconds=5) < seen["GW1"] < ago(55)


def test_the_migration_goes_down_and_up_again(migrated_db):
    run_alembic(migrated_db, "downgrade", "0001")
    inspector = inspect(migrated_db)
    assert "latest_message" not in inspector.get_table_names()
    assert {"last_seen", "last_will_at"}.isdisjoint(c["name"] for c in inspector.get_columns("device"))
    run_alembic(migrated_db, "upgrade", "head")
    assert "latest_message" in inspect(migrated_db).get_table_names()


# --- What GET /api/zones answers ---------------------------------------------------------


def test_a_zone_answers_with_its_latest_state(api, worker):
    outputs = {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "ON"}
    operation = {"cmdId": CMD_ID, "kind": "DOSE", "state": "EXECUTING", "line": "A"}
    handle(worker, zone_telemetry(ts=now_ts(2), leak=False, activeCmdId=CMD_ID), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(
        ts=now_ts(1), seq=40, outputs=outputs, operation=operation, latches=["LOW_LOW"], configVersion=7,
    ))
    handle(worker, heartbeat(ts=now_ts(1), seq=41))

    body = api.get("/api/zones/Z1").json()

    assert (body["online"], body["mode"], body["estop"], body["stopped"]) == (True, "REMOTE", False, False)
    assert body["equipment"] == outputs  # §6.8's outputs, under §11.2's name
    assert body["operation"] == operation
    assert (body["latches"], body["configVersion"]) == (["LOW_LOW"], 7)
    assert (body["leak"], body["activeCmdId"]) == (False, CMD_ID)
    assert api.get("/api/zones").json()["items"][0] == body


def test_state_fields_are_null_only_while_nothing_has_been_received(api, worker):
    handle(worker, zone_telemetry(ts=now_ts(), leak=True), "farm/siet-ph1/zone/Z1/telemetry")
    body = api.get("/api/zones/Z1").json()
    assert body["leak"] is True  # telemetry has arrived
    for field in ("mode", "estop", "stopped", "operation", "configVersion"):
        assert body[field] is None, field  # no ZONE_STATE yet
    assert (body["equipment"], body["latches"]) == ({}, [])
    zone2 = api.get("/api/zones/Z2").json()
    assert (zone2["leak"], zone2["activeCmdId"], zone2["mode"]) == (None, None, None)


def test_an_older_state_never_replaces_a_newer_one_in_the_answer(api, worker):
    handle(worker, zone_state(ts=now_ts(1), seq=30, mode="MAINT"))
    handle(worker, zone_state(ts=now_ts(1), seq=29, mode="REMOTE"))
    assert api.get("/api/zones/Z1").json()["mode"] == "MAINT"


def test_a_controllers_state_for_another_zone_doesnt_count(api, worker):
    handle(worker, zone_state(ts=now_ts(1), zone="Z2", mode="MAINT"), "farm/siet-ph1/zone/Z2/state")
    assert api.get("/api/zones/Z1").json()["mode"] is None


def test_the_last_will_shows_the_zone_offline_at_once(api, worker):
    handle(worker, zone_telemetry(ts=now_ts(2)), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(ts=now_ts(1), seq=50))
    assert api.get("/api/zones/Z1").json()["online"] is True

    handle(worker, LAST_WILL, "farm/siet-ph1/device/ZC1/status")

    body = api.get("/api/zones/Z1").json()
    assert body["online"] is False
    assert body["quality"]["ec"] == "STALE"
    assert body["mode"] == "REMOTE"  # the last state stays, marked by online: false
    device = next(d for d in api.get("/api/devices").json()["items"] if d["deviceId"] == "ZC1")
    assert device["online"] is False


def test_the_zone_is_online_again_after_the_device_comes_back(api, worker):
    handle(worker, LAST_WILL, "farm/siet-ph1/device/ZC1/status")
    handle(worker, heartbeat(ts=now_ts(SLOW_CLOCK_S), bootId=5, seq=1))  # its clock runs slow
    assert api.get("/api/zones/Z1").json()["online"] is True


def test_another_clients_state_never_shows(api, seeded, tmp_path):
    seed(seeded, write_pack(tmp_path / "client_B", LAYOUT | {"site": "another-site"}))
    other = Worker(seeded, "client_B", "another-site")
    handle(other, zone_state(ts=now_ts(), site="another-site", mode="MAINT"), "farm/another-site/zone/Z1/state")
    handle(other, LAST_WILL, "farm/another-site/device/ZC1/status")
    body = api.get("/api/zones/Z1").json()
    assert body["mode"] is None
    with seeded.connect() as connection:
        mine = connection.scalar(select(Device.last_will_at).where(Device.org_id == ORG, Device.device_id == "ZC1"))
    assert mine is None


# --- The rule itself ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seen_ago", "will_ago", "expected"),
    [
        (10, None, True),
        (10, 20, False),  # a last will is waiting: offline, whatever the two times say
        (10, 5, False),
        (OFFLINE_AFTER.total_seconds() - 1, None, True),
        (OFFLINE_AFTER.total_seconds(), None, False),
        (None, None, False),
        (None, 5, False),
    ],
)
def test_online_takes_the_last_will_into_account(seen_ago, will_ago, expected):
    now = datetime(2026, 11, 9, 5, tzinfo=UTC)
    seen = None if seen_ago is None else now - timedelta(seconds=seen_ago)
    will = None if will_ago is None else now - timedelta(seconds=will_ago)
    assert is_online(seen, now, will) is expected
