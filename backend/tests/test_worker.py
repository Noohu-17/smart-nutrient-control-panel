"""The worker (T1-11): valid, invalid and repeated messages, the database and the reconnects.

The messages are shaped as the simulator sends them (simulator/sim/messages.py, T4-5). The
database tests run on a throwaway database; test_worker_broker.py adds a real broker.
"""

import asyncio
import dataclasses
import json
import logging
import uuid
from datetime import UTC, datetime

import aiomqtt
import pytest
from sqlalchemy import select

from app.config import load_settings
from app.contract import parse_farm_message
from app.db.models import Reading
from app.worker import main as worker_main
from app.worker.main import RETRY_MAX_S, Worker, retry_delay, run
from app.worker.store import last_seen_times, reading_rows
from conftest import run_alembic

ORG = "client_A"
SITE = "siet-ph1"
TOPIC = f"farm/{SITE}/zone/Z1/telemetry"

TS = "2026-11-09T04:54:00.123Z"
SIM_TS = "2026-11-09T06:30:00.000Z"
PARSED_TS = datetime(2026, 11, 9, 4, 54, 0, 123000, tzinfo=UTC)
PARSED_SIM_TS = datetime(2026, 11, 9, 6, 30, tzinfo=UTC)


def envelope(kind, device):
    return {
        "v": 1, "type": kind, "msgId": str(uuid.uuid4()), "ts": TS, "site": SITE, "device": device,
        "bootId": 4, "seq": 18,
    }


def zone_telemetry(**changes):
    return envelope("ZONE_TELEMETRY", "ZC1") | {
        "simTs": SIM_TS,
        "zone": "Z1",
        "readings": {
            "ec": 1.62, "ph": None, "solTempC": 23.4, "reservoirPct": 78, "reservoirL": 936,
            "circFlowLpm": 31.0, "doMgL": None,
            "dayTankPct": {"A": 64, "B": 66, "ACID": 81},
            "dayTankL": {"A": 32.0, "B": 33.0, "ACID": 40.5},
        },
        "quality": {
            "ec": "OK", "ph": "FAULT", "solTempC": "OK", "reservoirPct": "OK", "circFlowLpm": "OK",
        },
        "equipment": {"Z1-CIRC": "ON"}, "mode": "REMOTE", "estop": False, "leak": False,
        "activeCmdId": None,
    } | changes


def climate_telemetry(**changes):
    return envelope("CLIMATE_TELEMETRY", "CC1") | {
        "readings": {
            "airTempC": 31.2, "airTempByPosC": {"PAD_END": 29.4, "FAN_END": 33.1}, "airRh": 71,
            "outsideTempC": 33.8, "outsideRh": 58, "elecRoomTempC": 34.0,
        },
        "quality": {"airTempC": "OK", "airRh": "OK"},
        "mode": "AUTOMATIC", "manualUntil": None, "equipment": {"fanStage": 2, "FAN1": "ON"},
        "runTimeTodayMin": {"FANS": 312},
        "setpoints": {"airTempMaxC": 30.0, "airRhMax": 85, "solTempMaxC": 24.0},
    } | changes


def power_state(**changes):
    return envelope("POWER_STATE", "PM1") | {
        "mains": "OK", "phases": {"l1V": 231, "l2V": None, "l3V": 233}, "source": "MAINS",
        "ats": "MAINS",
    } | changes


def heartbeat(device="ZC1", **changes):
    return envelope("DEVICE_STATUS", device) | {
        "online": True, "fw": "sim-0.1.0", "fwHash": None, "uptimeS": 120, "rssiDbm": None,
        "clockSynced": True, "configVersion": None, "freeHeapKb": None,
    } | changes


def zone_state(**changes):
    return envelope("ZONE_STATE", "ZC1") | {
        "zone": "Z1", "mode": "REMOTE", "estop": False, "stopped": False, "outputs": {"Z1-CIRC": "ON"},
        "operation": None, "latches": [], "configVersion": None,
    } | changes


def with_reading(**readings):
    return zone_telemetry(readings=zone_telemetry()["readings"] | readings)


def values_by_metric(rows):
    return {row["metric"]: row["value"] for row in rows}


def qualities_by_metric(rows):
    return {row["metric"]: row["quality"] for row in rows}


# --- From a message to rows -----------------------------------------------------------


def test_zone_telemetry_gives_one_row_per_value():
    rows = reading_rows(parse_farm_message(zone_telemetry()), ORG)
    assert values_by_metric(rows) == {
        "ec": 1.62, "ph": None, "solTempC": 23.4, "reservoirPct": 78, "reservoirL": 936,
        "circFlowLpm": 31.0, "doMgL": None,
        "dayTankPct.A": 64, "dayTankPct.B": 66, "dayTankPct.ACID": 81,
        "dayTankL.A": 32.0, "dayTankL.B": 33.0, "dayTankL.ACID": 40.5,
    }
    keys = {(r["org_id"], r["site_id"], r["device_id"], r["zone_id"]) for r in rows}
    assert keys == {(ORG, SITE, "ZC1", "Z1")}
    assert {(r["ts"], r["sim_ts"]) for r in rows} == {(PARSED_TS, PARSED_SIM_TS)}


def test_null_values_and_quality_stay_as_sent():
    rows = reading_rows(parse_farm_message(zone_telemetry()), ORG)
    qualities = qualities_by_metric(rows)
    assert (values_by_metric(rows)["ph"], qualities["ph"]) == (None, "FAULT")
    assert (values_by_metric(rows)["doMgL"], qualities["doMgL"]) == (None, None)  # no quality sent
    assert qualities["ec"] == "OK"
    assert qualities["reservoirL"] is None


def test_a_map_readings_quality_covers_every_entry():
    message = zone_telemetry(quality={"dayTankPct": "STALE"})
    qualities = qualities_by_metric(reading_rows(parse_farm_message(message), ORG))
    assert [qualities[f"dayTankPct.{line}"] for line in ("A", "B", "ACID")] == ["STALE"] * 3
    assert qualities["dayTankL.A"] is None


def test_climate_telemetry_has_no_zone_and_uses_the_sensor_positions():
    rows = reading_rows(parse_farm_message(climate_telemetry()), ORG)
    assert values_by_metric(rows) == {
        "airTempC": 31.2, "airTempByPosC.PAD_END": 29.4, "airTempByPosC.FAN_END": 33.1, "airRh": 71,
        "outsideTempC": 33.8, "outsideRh": 58, "elecRoomTempC": 34.0,
    }
    assert {r["zone_id"] for r in rows} == {None}
    assert {r["sim_ts"] for r in rows} == {None}  # no simTs: not a playground site


def test_power_state_with_and_without_a_ups():
    without_ups = reading_rows(parse_farm_message(power_state()), ORG)
    assert values_by_metric(without_ups) == {"l1V": 231, "l2V": None, "l3V": 233}

    ups = {"onBattery": False, "batteryPct": 100, "runtimeMin": None}
    with_ups = reading_rows(parse_farm_message(power_state(ups=ups, generator="STOPPED")), ORG)
    assert values_by_metric(with_ups) == {
        "l1V": 231, "l2V": None, "l3V": 233, "upsBatteryPct": 100, "upsRuntimeMin": None,
    }
    assert {r["zone_id"] for r in with_ups} == {None}


def test_a_heartbeat_gives_its_uptime():
    rows = reading_rows(parse_farm_message(heartbeat()), ORG)
    assert values_by_metric(rows) == {"uptimeS": 120}
    assert {(r["zone_id"], r["quality"]) for r in rows} == {(None, None)}


def test_a_zone_state_has_no_values():
    assert reading_rows(parse_farm_message(zone_state()), ORG) == []


# --- Through the worker, into the database ---------------------------------------------


@pytest.fixture
def worker(migrated_db):
    return Worker(migrated_db, ORG, SITE)


def handle(worker, message, topic=TOPIC):
    payload = message if isinstance(message, bytes) else json.dumps(message).encode()
    asyncio.run(worker.handle(topic, payload))


def stored_readings(engine):
    with engine.connect() as connection:
        rows = connection.execute(select(Reading.__table__).order_by(Reading.metric)).mappings()
        return [dict(row) for row in rows]


def last_seen(engine):
    return last_seen_times(engine, ORG, SITE)


def warnings_in(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_valid_message_lands_in_the_reading_table(worker):
    message = zone_telemetry()
    handle(worker, message)
    rows = stored_readings(worker.engine)

    expected = reading_rows(parse_farm_message(message), ORG)
    assert rows == sorted(expected, key=lambda row: row["metric"])
    ph = next(row for row in rows if row["metric"] == "ph")
    assert (ph["value"], ph["quality"]) == (None, "FAULT")
    assert {row["ts"] for row in rows} == {PARSED_TS}
    assert {row["sim_ts"] for row in rows} == {PARSED_SIM_TS}


def test_climate_power_and_heartbeats_land_too(worker):
    handle(worker, climate_telemetry(), "farm/x/climate/telemetry")
    handle(worker, power_state(), "farm/x/power/state")
    handle(worker, heartbeat("GW1"), "farm/x/device/GW1/status")
    assert {row["device_id"] for row in stored_readings(worker.engine)} == {"CC1", "PM1", "GW1"}


def test_a_replay_is_stored_once(worker, caplog):
    caplog.set_level(logging.INFO, logger="worker")
    message = zone_telemetry()
    handle(worker, message)
    first = stored_readings(worker.engine)

    handle(worker, message)
    handle(worker, message)

    assert stored_readings(worker.engine) == first
    assert sum("dropped a replay" in m for m in caplog.messages) == 2


def test_a_new_message_after_a_replay_is_still_stored(worker):
    message = zone_telemetry()
    handle(worker, message)
    handle(worker, message)
    handle(worker, zone_telemetry(ts="2026-11-09T04:55:00.000Z"))
    count = len(reading_rows(parse_farm_message(message), ORG))
    assert len(stored_readings(worker.engine)) == 2 * count


def test_last_seen_comes_from_heartbeats_and_telemetry(worker):
    assert last_seen(worker.engine) == {}
    handle(worker, heartbeat("ZC2"))
    handle(worker, zone_telemetry())
    assert last_seen(worker.engine) == {"ZC1": PARSED_TS, "ZC2": PARSED_TS}


def test_last_seen_never_moves_back_for_an_old_or_retained_message(worker):
    handle(worker, heartbeat())
    handle(worker, heartbeat(ts="2026-11-09T04:00:00.000Z"))
    assert last_seen(worker.engine) == {"ZC1": PARSED_TS}
    handle(worker, heartbeat(ts="2026-11-09T05:00:00.000Z"))
    assert last_seen(worker.engine) == {"ZC1": datetime(2026, 11, 9, 5, tzinfo=UTC)}


def test_last_seen_belongs_to_one_site(worker):
    handle(worker, heartbeat())
    assert last_seen_times(worker.engine, ORG, "another-site") == {}
    assert last_seen_times(worker.engine, "another-org", SITE) == {}


def test_the_last_will_is_accepted_and_stores_nothing(worker, caplog):
    handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False})
    assert stored_readings(worker.engine) == []
    assert warnings_in(caplog) == []


def test_a_zone_state_is_accepted_and_stores_nothing(worker, caplog):
    handle(worker, zone_state())
    assert stored_readings(worker.engine) == []
    assert warnings_in(caplog) == []


def test_a_device_the_layout_doesnt_know_keeps_its_readings(worker):
    handle(worker, zone_telemetry(device="ZC9"))
    assert {row["device_id"] for row in stored_readings(worker.engine)} == {"ZC9"}


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (b"not json", "not JSON"),
        (b"\xff\xfe", "not JSON"),
        (b"[" * 100_000, "not JSON"),  # deeper than the parser can go
        (b"[1, 2]", "not a JSON object"),
        ({"v": 1}, "no message type"),
        ({"v": 1, "type": ["ZONE_TELEMETRY"]}, "no message type"),
        (zone_telemetry(zone="Zone A"), "zone"),
        (with_reading(ec="1.62"), "ec"),
        (zone_telemetry(readings=with_reading(ph=99)["readings"], quality={"ph": "OK"}), "ph"),
        (zone_telemetry(ts="2026-11-09T10:24:00.000+05:30"), "ts"),
        (zone_telemetry(msgId="not-a-uuid"), "msgId"),
        (json.dumps(with_reading(solTempC=float("nan"))).encode(), "solTempC"),
        (json.dumps(with_reading(solTempC=float("inf"))).encode(), "solTempC"),
        (zone_telemetry(device="ZC\ud800"), "device"),
        (zone_telemetry(site="another-site"), "another-site"),
    ],
    ids=[
        "not-json", "not-utf8", "too-deep", "not-an-object", "no-type", "type-not-a-string",
        "bad-zone-id", "number-as-string", "against-its-quality", "ts-not-utc", "bad-msg-id",
        "nan", "infinity", "lone-surrogate", "another-site",
    ],
)
def test_a_bad_message_is_logged_and_skipped_and_the_next_one_is_stored(
    worker, caplog, message, reason
):
    handle(worker, message)

    assert stored_readings(worker.engine) == []
    (warning,) = warnings_in(caplog)
    assert "skipping a bad message" in warning and TOPIC in warning and reason in warning

    handle(worker, zone_telemetry())
    assert stored_readings(worker.engine) != []


@pytest.mark.parametrize(
    "changes",
    [
        {"device": "x" * 65},
        {"device": "ZC\u0000"},
        {"readings": with_reading(dayTankPct={"y" * 200: 1})["readings"]},
    ],
    ids=["device-longer-than-its-column", "nul-character", "metric-longer-than-its-column"],
)
def test_a_message_the_database_refuses_is_skipped_not_retried(worker, caplog, changes):
    async def no_retries(seconds):
        raise AssertionError("a bad message must not be retried")

    worker._sleep = no_retries
    handle(worker, zone_telemetry(**changes))

    assert stored_readings(worker.engine) == []
    assert any("the database refused" in w for w in warnings_in(caplog))
    handle(worker, zone_telemetry())
    assert stored_readings(worker.engine) != []


@pytest.mark.parametrize("ts", ["0001-01-01T00:00:00.000Z", "9999-12-31T23:59:59.000Z"])
def test_a_time_at_the_ends_of_the_calendar_never_blocks_the_worker(worker, ts):
    async def no_retries(seconds):
        raise AssertionError("a bad message must not be retried")

    worker._sleep = no_retries
    handle(worker, zone_telemetry(ts=ts))
    handle(worker, heartbeat("ZC2"))
    assert "ZC2" in last_seen(worker.engine)


@pytest.mark.parametrize("kind", ["EVENT", "DOSE", "CMD_STATE"])
def test_a_type_with_no_model_yet_is_skipped_quietly(worker, caplog, kind):
    handle(worker, {"v": 1, "type": kind})
    assert stored_readings(worker.engine) == []
    assert warnings_in(caplog) == []


def test_a_message_waits_until_the_database_is_ready(empty_db, caplog):
    """The worker can start before `alembic upgrade head`: nothing is lost, and it says why."""
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            run_alembic(empty_db, "upgrade", "head")

    worker = Worker(empty_db, ORG, SITE, sleep=sleep)
    handle(worker, zone_telemetry())

    assert sleeps == [1, 2, 4]
    assert stored_readings(empty_db) != []
    warnings = warnings_in(caplog)
    assert len(warnings) == 3 and all("database not ready" in w for w in warnings)


# --- The connection, with a fake MQTT client --------------------------------------------


@dataclasses.dataclass
class FakeMessage:
    topic: str
    payload: bytes


class Stop(Exception):
    """Ends the worker's endless loop at the end of a test."""


class FakeClient:
    """Async context manager standing in for aiomqtt.Client: `plan` is one connection's fate."""

    def __init__(self, plan):
        self.plan = plan
        self.subscribed = []

    async def __aenter__(self):
        if self.plan == "refuse":
            raise aiomqtt.MqttError("connection refused")
        if self.plan == "stop":
            raise Stop
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, topic, qos):
        self.subscribed.append((topic, qos))

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        for message in self.plan:
            yield message
        raise aiomqtt.MqttError("connection lost")


@pytest.fixture
def settings(tmp_path):
    """Settings for a config pack holding only what the worker reads from it."""
    client_dir = tmp_path / ORG
    client_dir.mkdir()
    (client_dir / "layout.json").write_text(json.dumps({"site": SITE}), encoding="utf-8")
    return dataclasses.replace(load_settings(), client_dir=client_dir)


def run_with_plan(settings, plans):
    """Run the worker through scripted connections; return (client kwargs, clients, sleeps)."""
    calls, clients, sleeps = [], [], []
    plans = iter(plans)

    def factory(**kwargs):
        calls.append(kwargs)
        clients.append(FakeClient(next(plans)))
        return clients[-1]

    async def sleep(seconds):
        sleeps.append(seconds)

    with pytest.raises(Stop):
        asyncio.run(run(settings, client_factory=factory, sleep=sleep))
    return calls, clients, sleeps


def test_retry_delay_doubles_up_to_a_cap():
    assert [retry_delay(n) for n in range(7)] == [1, 2, 4, 8, 16, RETRY_MAX_S, RETRY_MAX_S]


def test_connects_as_cloud_worker_with_a_persistent_session(settings):
    calls, _, _ = run_with_plan(settings, ["stop"])
    assert calls[0]["identifier"] == "CLOUD-worker"
    assert calls[0]["clean_session"] is False
    assert calls[0]["protocol"] == aiomqtt.ProtocolVersion.V311


def test_reconnects_with_a_growing_delay_and_subscribes_again(settings, caplog):
    caplog.set_level(logging.INFO, logger="worker")
    _, clients, sleeps = run_with_plan(settings, ["refuse", "refuse", [], "refuse", [], "stop"])

    assert sleeps == [1, 2, 1, 2, 1]  # the delay starts again after a successful connect
    subscribed = [client.subscribed for client in clients if client.plan == []]
    assert subscribed == [[(f"farm/{SITE}/#", 1)]] * 2
    assert sum("connected to" in m for m in caplog.messages) == 2
    assert sum("not connected" in m for m in caplog.messages) == 5


def test_messages_from_the_broker_reach_the_database(settings, migrated_db):
    settings = dataclasses.replace(settings, db_name=migrated_db.url.database)
    message = FakeMessage(TOPIC, json.dumps(zone_telemetry()).encode())
    run_with_plan(settings, [[message], "stop"])
    assert stored_readings(migrated_db) != []
    assert {row["org_id"] for row in stored_readings(migrated_db)} == {ORG}  # the folder's name


def test_runs_on_a_loop_that_works_on_windows(monkeypatch):
    """aiomqtt can't run on Windows' default proactor event loop."""
    loops = []

    async def fake_run(settings):
        loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(worker_main, "run", fake_run)
    worker_main.main()
    assert loops
    assert not isinstance(loops[0], getattr(asyncio, "ProactorEventLoop", ()))
