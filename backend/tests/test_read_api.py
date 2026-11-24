"""The read API (T1-12): GET /api/farm, /api/zones, /api/zones/{zoneId} and /api/devices.

The database tests use a throwaway database. Readings go in through the worker's own code, so
these also show that the API and the worker agree on what a row means.
"""

import copy
import dataclasses
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
from test_worker import (
    SITE,
    climate_telemetry,
    heartbeat,
    power_state,
    zone_state,
    zone_telemetry,
)

from app.api.routes import MAX_PAGE, _engine_for, get_engine, get_settings
from app.api.schemas import ZoneStatus, contract_time
from app.api.status import FRESH_FOR, OFFLINE_AFTER, is_online, zone_status
from app.config import load_settings
from app.contract import parse_farm_message
from app.db.models import Reading
from app.db.seed import seed
from app.main import app
from app.worker.store import last_seen_times, reading_rows, store_message
from conftest import sign_in_as

ORG = "client_A"
CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "contract.md"
REPO_CLIENTS = Path(__file__).resolve().parents[2] / "config" / "clients"
READING_NAMES = {
    "ec", "ph", "solTempC", "reservoirPct", "reservoirL", "circFlowLpm", "doMgL", "dayTankPct", "dayTankL",
}


def layout_zone(number: int) -> dict:
    return {
        "id": f"Z{number}", "name": f"Zone {number}", "controller": f"ZC{number}",
        "reservoir": {"id": f"R{number}", "usableL": 1200},
        "dosingCabinet": f"D{number}",
        "doseLines": ["A", "B", "ACID"],
        "dayTanks": {line: {"usableL": 50} for line in ("A", "B", "ACID")},
        "benches": [{"id": f"Z{number}-B1", "channels": 6, "lengthM": 8.5}],
    }


LAYOUT = {
    "v": 1, "site": SITE, "name": "A farm", "timezone": "Asia/Kolkata",
    "zones": [layout_zone(1), layout_zone(2)],
    "nursery": {"tables": 4},
    "climate": {
        "controller": "CC1", "fans": ["FAN1"], "padPumps": ["PAD1"], "chillers": {"Z1": "CH1"},
        "shade": ["SHADE1"], "haf": ["HAF1"], "foggers": [], "airSensors": ["PAD_END"],
    },
    "power": {"monitor": "PM1", "generator": True, "ups": True},
    "devices": [
        {"id": "GW1", "kind": "GATEWAY"},
        {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"},
        {"id": "ZC2", "kind": "ZONE_CONTROLLER", "zone": "Z2"},
        {"id": "CC1", "kind": "CLIMATE_CONTROLLER"},
        {"id": "PM1", "kind": "POWER_MONITOR"},
    ],
}
FEATURES = {"playground": True, "demoFarm": False}


def farm_answer(layout: dict, features: dict = FEATURES) -> dict:
    """GET /api/farm: the layout and its features, with each zone's source filled in where the
    layout leaves it out: SIMULATED on a playground site, LIVE elsewhere (§2.4)."""
    default = "SIMULATED" if features.get("playground") is True else "LIVE"
    return layout | {
        "zones": [{"source": default, **z} for z in layout["zones"]],
        "climate": {"source": default, **layout["climate"]},
        "features": features,
    }


def write_pack(folder: Path, layout: dict = LAYOUT, features: dict = FEATURES) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    (folder / "features.json").write_text(json.dumps(features), encoding="utf-8")
    return folder


@pytest.fixture
def pack(tmp_path) -> Path:
    return write_pack(tmp_path / ORG)


def api_client(client_dir: Path, engine, role: str = "OWNER") -> TestClient:
    """A client signed in as `role` (conftest.sign_in_as): these tests aren't about signing in."""
    settings = dataclasses.replace(load_settings(), client_dir=client_dir)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    sign_in_as(app, role)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def no_database():
    """An engine that fails if anything tries to use it."""
    return create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})


@pytest.fixture
def api(migrated_db, pack):
    seed(migrated_db, pack)
    return api_client(pack, migrated_db)


def ago(seconds: float) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def store(engine, message: dict, org: str = ORG) -> None:
    """As the worker does: its readings, the latest state and last_seen, in one transaction."""
    parsed = parse_farm_message(message)
    store_message(engine, parsed, org, reading_rows(parsed, org), datetime.now(UTC))


def fresh_zone(seconds: float = 5, **changes) -> dict:
    return zone_telemetry(ts=contract_time(ago(seconds)), **changes)


def assert_error(response, status: int, code: str) -> dict:
    """The §11.1 shape, and nothing of FastAPI's own."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    return body["error"]


def contract_zone_example() -> dict:
    text = CONTRACT.read_text(encoding="utf-8")
    after = text[text.index("**`GET /zones/{zoneId}`**"):]
    return json.loads(re.search(r"```json\n(.*?)```", after, re.DOTALL).group(1))


# --- GET /api/farm ----------------------------------------------------------------------


def test_farm_is_the_layout_plus_its_features(api):
    response = api.get("/api/farm")
    assert response.status_code == 200
    assert response.json() == farm_answer(LAYOUT)


def test_farm_answers_for_the_real_config_pack_as_the_fake_api_does():
    """The fake API (app/src/mocks/data/layout.ts) answers these two files in the same way."""
    client_dir = load_settings().client_dir
    if not client_dir.exists():
        pytest.skip("no config pack here (the Docker test image has none)")
    layout = json.loads((client_dir / "layout.json").read_text(encoding="utf-8"))
    features = json.loads((client_dir / "features.json").read_text(encoding="utf-8"))
    sign_in_as(app)
    client = TestClient(app)
    assert client.get("/api/farm").json() == layout | {"features": features}


def test_farm_never_sends_meta_blocks(tmp_path, no_database):
    layout = copy.deepcopy(LAYOUT) | {"_meta": {"placeholder": True}}
    layout["zones"][0]["_meta"] = {"note": "inside a zone"}
    folder = write_pack(tmp_path / ORG, layout, FEATURES | {"_meta": {"note": "in the features"}})
    assert api_client(folder, no_database).get("/api/farm").json() == farm_answer(LAYOUT)


def test_farm_passes_on_fields_the_model_doesnt_name(tmp_path, no_database):
    layout = copy.deepcopy(LAYOUT) | {"newTopLevel": {"a": 1}}
    layout["zones"][0]["sensors"] = {"dissolvedOxygen": True}
    layout["zones"][0]["newInZone"] = [1, 2]
    folder = write_pack(tmp_path / ORG, layout)
    assert api_client(folder, no_database).get("/api/farm").json() == farm_answer(layout)


def test_farm_leaves_out_what_the_pack_leaves_out(api):
    assert "sensors" not in api.get("/api/farm").json()["zones"][0]
    assert "zone" not in api.get("/api/farm").json()["devices"][0]


def test_farm_needs_no_database(pack, no_database):
    assert api_client(pack, no_database).get("/api/farm").status_code == 200


# --- GET /api/sensors (DM-3) ------------------------------------------------------------


@pytest.mark.parametrize("client", ["client_A", "client_B"])
def test_sensors_is_the_packs_catalogue_without_meta(client, no_database):
    folder = REPO_CLIENTS / client
    if not folder.exists():
        pytest.skip("no config pack here (the Docker test image has none)")
    catalogue = json.loads((folder / "sensors.json").read_text(encoding="utf-8"))
    answer = api_client(folder, no_database).get("/api/sensors")
    assert answer.status_code == 200
    assert answer.json() == {key: value for key, value in catalogue.items() if key != "_meta"}
    assert len(answer.json()["farmTypes"]["HYDROPONICS"]["sensors"]) == 10  # the sheet's hydroponics rows
    assert answer.json()["growingSystems"]["NFT"] == "HYDROPONICS"


def test_sensors_is_404_for_a_pack_without_a_catalogue(pack, no_database):
    error = assert_error(api_client(pack, no_database).get("/api/sensors"), 404, "NOT_FOUND")
    assert "no sensor catalogue" in error["message"]


def test_a_broken_sensor_catalogue_is_a_500(pack, no_database, caplog):
    (pack / "sensors.json").write_text(json.dumps({"growingSystems": {}}), encoding="utf-8")
    assert_error(api_client(pack, no_database).get("/api/sensors"), 500, "INTERNAL")
    assert any("cannot load the config pack" in m for m in caplog.messages)


@pytest.mark.parametrize(
    "break_pack",
    [
        lambda folder: (folder / "features.json").unlink(),
        lambda folder: (folder / "layout.json").unlink(),
        lambda folder: (folder / "layout.json").write_text("{ not json", encoding="utf-8"),
        lambda folder: (folder / "layout.json").write_text("[1, 2]", encoding="utf-8"),
        lambda folder: (folder / "layout.json").write_text(json.dumps({"v": 1}), encoding="utf-8"),
    ],
    ids=["no-features", "no-layout", "not-json", "not-an-object", "fields-missing"],
)
def test_a_broken_pack_is_a_clear_error_with_nothing_private_in_it(pack, no_database, break_pack, caplog):
    break_pack(pack)
    error = assert_error(api_client(pack, no_database).get("/api/farm"), 500, "INTERNAL")
    assert error["message"] == "Something went wrong on the server"
    assert str(pack) not in json.dumps(error)
    assert any("cannot load the config pack" in m for m in caplog.messages)


# --- GET /api/zones/{zoneId} ------------------------------------------------------------


def test_a_zone_returns_its_latest_readings(api, migrated_db):
    message = fresh_zone()
    store(migrated_db, message)
    store(migrated_db, heartbeat(ts=message["ts"]))

    response = api.get("/api/zones/Z1")

    assert response.status_code == 200
    body = response.json()
    assert body["zone"] == "Z1"
    assert body["online"] is True
    assert body["lastSeen"] == message["ts"]
    assert body["ts"] == message["ts"]
    assert body["simTs"] == message["simTs"]
    assert body["readings"] == message["readings"]
    assert body["quality"] == message["quality"]


def test_a_zone_has_exactly_the_fields_of_the_contract_example(api, migrated_db):
    example = contract_zone_example()
    message = fresh_zone()
    del message["simTs"]
    store(migrated_db, message)
    body = api.get("/api/zones/Z1").json()
    assert set(body) == set(example)
    assert set(body["readings"]) == set(example["readings"]) == READING_NAMES


def test_the_contract_example_goes_through_the_model_unchanged():
    example = contract_zone_example()
    assert ZoneStatus.model_validate(example).model_dump(mode="json", exclude_unset=True) == example


def test_a_zone_that_has_never_reported_answers_as_the_contract_says(api):
    """§11.2: 200, not online, every reading null, quality and equipment {}, latches []."""
    response = api.get("/api/zones/Z2")
    assert response.status_code == 200
    assert response.json() == {
        "zone": "Z2", "source": "SIMULATED", "online": False, "lastSeen": None, "ts": None,
        "readings": {
            "ec": None, "ph": None, "solTempC": None, "reservoirPct": None, "reservoirL": None,
            "circFlowLpm": None, "doMgL": None,
            "dayTankPct": {"A": None, "B": None, "ACID": None},
            "dayTankL": {"A": None, "B": None, "ACID": None},
        },
        "quality": {}, "equipment": {}, "mode": None, "estop": None, "leak": None, "stopped": None,
        "activeCmdId": None, "operation": None, "latches": [], "configVersion": None,
        # what the zone is comes from the pack, whether or not it has reported (T1-38). This
        # layout says nothing of it and the pack has no io_map.json or recipes.json.
        "profile": {
            "name": "Zone 2", "growingSystem": None, "growingUnits": [], "plantSites": None,
            "size": None, "position": None, "tank": {"id": "R2", "usableL": 1200}, "sensors": {},
            "controller": "ZC2", "link": "MQTT", "doseLines": ["A", "B", "ACID"], "notes": None,
        },
        "crop": None,
    }


def test_the_state_comes_from_zone_state_and_leak_from_telemetry(api, migrated_db):
    """ZONE_STATE is authoritative where it overlaps telemetry (§6.8); leak is telemetry's (§11.2)."""
    store(migrated_db, fresh_zone(mode="MAINT", estop=True, leak=True))
    store(migrated_db, zone_state(ts=contract_time(ago(1)), stopped=True, latches=["LEAK"]))
    body = api.get("/api/zones/Z1").json()
    assert (body["mode"], body["estop"], body["stopped"]) == ("REMOTE", False, True)
    assert (body["equipment"], body["latches"]) == ({"Z1-CIRC": "ON"}, ["LEAK"])
    assert body["leak"] is True


def test_the_newest_message_wins_whatever_order_they_came_in(api, migrated_db):
    readings = zone_telemetry()["readings"]
    new = fresh_zone(5, readings=readings | {"ec": 2.0})
    old = fresh_zone(50, readings=readings | {"ec": 1.0})
    store(migrated_db, new)
    store(migrated_db, old)  # a gateway replay, or a late message
    body = api.get("/api/zones/Z1").json()
    assert (body["readings"]["ec"], body["ts"]) == (2.0, new["ts"])


def test_a_reading_is_one_messages_values_never_a_mix(api, migrated_db):
    store(migrated_db, fresh_zone(50, readings=zone_telemetry()["readings"] | {"ec": 1.0, "solTempC": 19.0}))
    store(
        migrated_db,
        fresh_zone(
            5, readings=zone_telemetry()["readings"] | {"ec": 2.0, "solTempC": None}, quality={"ec": "OK"}
        ),
    )
    body = api.get("/api/zones/Z1").json()
    assert (body["readings"]["ec"], body["readings"]["solTempC"]) == (2.0, None)
    assert body["quality"] == {"ec": "OK"}  # not solTempC's from the older message


def test_zones_dont_see_each_others_readings(api, migrated_db):
    store(migrated_db, fresh_zone(device="ZC2", zone="Z2", readings=zone_telemetry()["readings"] | {"ec": 1.11}))
    store(migrated_db, fresh_zone(readings=zone_telemetry()["readings"] | {"ec": 1.99}))
    assert api.get("/api/zones/Z1").json()["readings"]["ec"] == 1.99
    assert api.get("/api/zones/Z2").json()["readings"]["ec"] == 1.11


def test_a_controller_moved_to_another_zone_leaves_its_old_readings_behind(api, migrated_db):
    readings = zone_telemetry()["readings"]
    store(migrated_db, fresh_zone(50, readings=readings | {"ec": 1.0}))
    store(migrated_db, fresh_zone(5, zone="Z2", readings=readings | {"ec": 2.0}))  # ZC1, for Z2
    assert api.get("/api/zones/Z1").json()["readings"]["ec"] == 1.0


def test_an_offline_controllers_readings_are_stale_however_new_they_are():
    """§11.2 gives both reasons. Today an offline controller's readings are old anyway."""
    now = datetime.now(UTC)
    row = SimpleNamespace(metric="ec", value=1.62, quality="OK", ts=now, sim_ts=None)
    status = zone_status("Z1", "SIMULATED", ["A"], now - OFFLINE_AFTER, [row], now)
    assert status.online is False
    assert status.quality == {"ec": "STALE"}


def test_an_offline_zone_still_answers_with_its_last_values(api, migrated_db):
    message = fresh_zone(OFFLINE_AFTER.total_seconds() + 20)
    store(migrated_db, message)
    response = api.get("/api/zones/Z1")
    assert response.status_code == 200
    body = response.json()
    assert body["online"] is False
    assert body["lastSeen"] == message["ts"]
    assert body["readings"]["ec"] == message["readings"]["ec"]
    assert body["quality"]["ec"] == "STALE"


def test_a_heartbeat_keeps_a_zone_online_but_not_its_readings_fresh(api, migrated_db):
    message = fresh_zone(FRESH_FOR.total_seconds() + 20)
    beat = heartbeat(ts=contract_time(ago(2)))
    store(migrated_db, message)
    store(migrated_db, beat)
    body = api.get("/api/zones/Z1").json()
    assert body["online"] is True
    assert body["lastSeen"] == beat["ts"]
    assert body["ts"] == message["ts"]  # when the latest reading was taken
    assert body["quality"]["ec"] == "STALE"


@pytest.mark.parametrize(
    ("age", "sent", "served"),
    [
        (5, "OK", "OK"),
        (FRESH_FOR.total_seconds() - 20, "OK", "OK"),
        (FRESH_FOR.total_seconds() + 5, "OK", "STALE"),
        (FRESH_FOR.total_seconds() + 5, "STALE", "STALE"),
        (FRESH_FOR.total_seconds() + 5, "OUT_OF_RANGE", "OUT_OF_RANGE"),
        (FRESH_FOR.total_seconds() + 5, "UNCALIBRATED", "UNCALIBRATED"),
        (OFFLINE_AFTER.total_seconds() + 5, "OUT_OF_RANGE", "OUT_OF_RANGE"),
        (OFFLINE_AFTER.total_seconds() + 5, "OK", "STALE"),
    ],
)
def test_ok_goes_stale_and_every_other_quality_stays(api, migrated_db, age, sent, served):
    store(migrated_db, fresh_zone(age, quality={"solTempC": sent}))
    assert api.get("/api/zones/Z1").json()["quality"]["solTempC"] == served


def test_fault_stays_fault_with_a_null_value(api, migrated_db):
    readings = zone_telemetry()["readings"] | {"ec": None}
    store(migrated_db, fresh_zone(FRESH_FOR.total_seconds() + 5, readings=readings, quality={"ec": "FAULT"}))
    body = api.get("/api/zones/Z1").json()
    assert (body["readings"]["ec"], body["quality"]["ec"]) == (None, "FAULT")


def test_a_reading_with_no_quality_has_none_in_the_answer(api, migrated_db):
    store(migrated_db, fresh_zone(quality={"ec": "OK"}))
    assert api.get("/api/zones/Z1").json()["quality"] == {"ec": "OK"}


def test_a_map_readings_quality_comes_back_as_it_was_sent(api, migrated_db):
    store(migrated_db, fresh_zone(quality={"dayTankPct": "UNCALIBRATED"}))
    assert api.get("/api/zones/Z1").json()["quality"] == {"dayTankPct": "UNCALIBRATED"}


def test_times_are_utc_with_milliseconds_and_z(api, migrated_db):
    store(migrated_db, zone_telemetry(ts="2026-11-09T04:54:00.100Z", simTs="2026-11-09T06:30:00.000Z"))
    body = api.get("/api/zones/Z1").json()
    assert (body["ts"], body["simTs"]) == ("2026-11-09T04:54:00.100Z", "2026-11-09T06:30:00.000Z")
    assert body["lastSeen"] == "2026-11-09T04:54:00.100Z"


def test_contract_time_converts_to_utc():
    from datetime import timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    assert contract_time(datetime(2026, 11, 9, 10, 24, 0, 5000, tzinfo=ist)) == "2026-11-09T04:54:00.005Z"


def test_sim_ts_is_left_out_unless_the_reading_has_it(api, migrated_db):
    message = fresh_zone()
    del message["simTs"]
    store(migrated_db, message)
    assert "simTs" not in api.get("/api/zones/Z1").json()


@pytest.mark.parametrize(
    "zone_id",
    ["Z9", "Z99999999999999999999", "z1", "abc", "Z", "Z1%20", "Z1'--", "Z1%00", "%F0%9F%8C%B1"],
)
def test_an_unknown_zone_is_404(api, zone_id):
    assert_error(api.get(f"/api/zones/{zone_id}"), 404, "NOT_FOUND")


def test_the_404_names_the_zone_like_the_fake_api(api):
    assert api.get("/api/zones/Z9").json()["error"]["message"] == "There is no zone Z9"


def test_a_zone_id_that_cant_be_one_doesnt_reach_the_database(pack, no_database):
    assert_error(api_client(pack, no_database).get("/api/zones/abc"), 404, "NOT_FOUND")


# --- GET /api/zones ---------------------------------------------------------------------


def test_zones_lists_the_same_object_for_every_zone(api, migrated_db):
    store(migrated_db, fresh_zone())
    response = api.get("/api/zones")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items"}
    assert [zone["zone"] for zone in body["items"]] == ["Z1", "Z2"]
    assert body["items"][0] == api.get("/api/zones/Z1").json()
    assert body["items"][1] == api.get("/api/zones/Z2").json()


def test_zones_come_in_layout_order_not_alphabetical(tmp_path, migrated_db):
    layout = copy.deepcopy(LAYOUT)
    layout["zones"] = [layout_zone(number) for number in (1, 2, 10)]
    layout["devices"] = layout["devices"] + [{"id": "ZC10", "kind": "ZONE_CONTROLLER", "zone": "Z10"}]
    folder = write_pack(tmp_path / ORG, layout)
    seed(migrated_db, folder)
    items = api_client(folder, migrated_db).get("/api/zones").json()["items"]
    assert [zone["zone"] for zone in items] == ["Z1", "Z2", "Z10"]


def test_dose_lines_come_from_the_layout_not_from_code(tmp_path, migrated_db):
    layout = copy.deepcopy(LAYOUT)
    layout["zones"][0] |= {"doseLines": ["A", "BASE"], "dayTanks": {"A": {"usableL": 50}, "BASE": {"usableL": 50}}}
    folder = write_pack(tmp_path / ORG, layout)
    seed(migrated_db, folder)
    readings = api_client(folder, migrated_db).get("/api/zones/Z1").json()["readings"]
    assert readings["dayTankPct"] == {"A": None, "BASE": None}


# --- GET /api/devices -------------------------------------------------------------------


def test_devices_lists_every_device_with_last_seen_and_online(api, migrated_db):
    recent, old = contract_time(ago(5)), contract_time(ago(OFFLINE_AFTER.total_seconds() + 20))
    store(migrated_db, heartbeat("GW1", ts=recent))
    store(migrated_db, zone_telemetry(ts=old))

    response = api.get("/api/devices")

    assert response.status_code == 200
    body = response.json()
    assert body["nextCursor"] is None
    assert body["items"] == [
        {"deviceId": "CC1", "kind": "CLIMATE_CONTROLLER", "zone": None, "online": False, "lastSeen": None},
        {"deviceId": "GW1", "kind": "GATEWAY", "zone": None, "online": True, "lastSeen": recent},
        {"deviceId": "PM1", "kind": "POWER_MONITOR", "zone": None, "online": False, "lastSeen": None},
        {"deviceId": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1", "online": False, "lastSeen": old},
        {"deviceId": "ZC2", "kind": "ZONE_CONTROLLER", "zone": "Z2", "online": False, "lastSeen": None},
    ]


def test_every_kind_of_stored_message_counts_as_seen(api, migrated_db):
    moment = contract_time(ago(5))
    store(migrated_db, climate_telemetry(ts=moment))
    store(migrated_db, power_state(ts=moment))
    store(migrated_db, heartbeat("GW1", ts=moment))
    store(migrated_db, zone_telemetry(ts=moment))
    seen = {d["deviceId"]: d["lastSeen"] for d in api.get("/api/devices").json()["items"]}
    assert seen == {"CC1": moment, "PM1": moment, "GW1": moment, "ZC1": moment, "ZC2": None}


def test_last_seen_agrees_with_the_worker_on_every_device(api, migrated_db):
    for seconds, message in [
        (300, zone_telemetry), (40, zone_telemetry), (70, heartbeat), (20, climate_telemetry),
        (90, power_state),
    ]:
        store(migrated_db, message(ts=contract_time(ago(seconds))))
    expected = last_seen_times(migrated_db, ORG, SITE)
    assert set(expected) == {"ZC1", "CC1", "PM1"}
    listed = {d["deviceId"]: d["lastSeen"] for d in api.get("/api/devices").json()["items"] if d["lastSeen"]}
    assert listed == {device: contract_time(moment) for device, moment in expected.items()}


def test_last_seen_never_moves_back(api, migrated_db):
    new, old = contract_time(ago(5)), contract_time(ago(500))
    store(migrated_db, heartbeat(ts=new))
    store(migrated_db, heartbeat(ts=old))
    store(migrated_db, zone_telemetry(ts=old))
    assert api.get("/api/zones/Z1").json()["lastSeen"] == new


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), True),
        (OFFLINE_AFTER - timedelta(milliseconds=1), True),
        (OFFLINE_AFTER, False),  # "3 minutes without any message"
        (OFFLINE_AFTER + timedelta(milliseconds=1), False),
    ],
)
def test_online_flips_at_exactly_three_minutes(age, expected):
    now = datetime(2026, 11, 9, 5, tzinfo=UTC)
    assert is_online(now - age, now) is expected


def test_a_device_never_heard_from_is_not_online():
    assert is_online(None, datetime.now(UTC)) is False


def test_the_time_limits_are_the_contracts():
    """§14 is the agreement: changing either value here, or there, has to fail."""
    timing = CONTRACT.read_text(encoding="utf-8").partition("## 14. Timing constants")[2]
    assert re.search(r"Device \*\*offline\*\* \| 3 minutes without any message", timing)
    assert re.search(r"Fresh EC/pH to dose \| ≤ 120 s old", timing)
    assert OFFLINE_AFTER == timedelta(minutes=3)
    assert FRESH_FOR == timedelta(seconds=120)


def test_devices_can_be_read_page_by_page(api):
    first = api.get("/api/devices", params={"limit": 2}).json()
    assert [d["deviceId"] for d in first["items"]] == ["CC1", "GW1"]
    assert first["nextCursor"] == "GW1"

    second = api.get("/api/devices", params={"limit": 2, "cursor": first["nextCursor"]}).json()
    assert [d["deviceId"] for d in second["items"]] == ["PM1", "ZC1"]

    last = api.get("/api/devices", params={"limit": 2, "cursor": second["nextCursor"]}).json()
    assert [d["deviceId"] for d in last["items"]] == ["ZC2"]
    assert last["nextCursor"] is None


def test_a_page_that_ends_exactly_at_the_last_device_has_no_next_cursor(api):
    assert api.get("/api/devices", params={"limit": 5}).json()["nextCursor"] is None
    assert api.get("/api/devices", params={"limit": 4}).json()["nextCursor"] == "ZC1"


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": MAX_PAGE + 1}, {"limit": "many"}, {"cursor": ""}, {"cursor": "x" * 65}, {"cursor": "Z\x00"}],
    ids=["limit-0", "limit-too-big", "limit-not-a-number", "empty-cursor", "long-cursor", "nul-in-cursor"],
)
def test_a_bad_page_request_is_422_in_the_contract_shape(pack, no_database, params):
    error = assert_error(api_client(pack, no_database).get("/api/devices", params=params), 422, "VALIDATION_ERROR")
    field = next(iter(params))
    assert error["details"]["fields"][0]["field"] == f"query.{field}"


def test_the_largest_page_is_allowed(api):
    assert api.get("/api/devices", params={"limit": MAX_PAGE}).status_code == 200


# --- Scoped by org_id and site_id --------------------------------------------------------


@pytest.fixture
def another_client(tmp_path, migrated_db):
    """Another client's site in the same database, with the same zone and device IDs."""
    folder = write_pack(tmp_path / "client_B", LAYOUT | {"site": "another-site"})
    seed(migrated_db, folder)
    readings = zone_telemetry()["readings"] | {"ec": 9.99}
    moment = contract_time(ago(1))
    message = zone_telemetry(site="another-site", ts=moment, readings=readings)
    store(migrated_db, message, org="client_B")
    store(migrated_db, heartbeat(site="another-site", ts=moment), org="client_B")
    return folder


def test_another_clients_data_never_shows(api, migrated_db, another_client):
    assert api.get("/api/zones/Z1").json()["readings"]["ec"] is None
    assert api.get("/api/zones/Z1").json()["lastSeen"] is None
    assert [zone["ts"] for zone in api.get("/api/zones").json()["items"]] == [None, None]
    assert [d["lastSeen"] for d in api.get("/api/devices").json()["items"]] == [None] * 5


def test_each_client_sees_its_own_data(migrated_db, pack, another_client):
    seed(migrated_db, pack)
    store(migrated_db, fresh_zone(readings=zone_telemetry()["readings"] | {"ec": 1.5}))
    ours = api_client(pack, migrated_db).get("/api/zones/Z1").json()
    theirs = api_client(another_client, migrated_db).get("/api/zones/Z1").json()
    assert (ours["readings"]["ec"], theirs["readings"]["ec"]) == (1.5, 9.99)


def test_the_org_is_part_of_the_scope_as_well_as_the_site(api, migrated_db):
    """A reading under the same site but another org is someone else's."""
    row = {
        "org_id": "another-org", "site_id": SITE, "device_id": "ZC1", "zone_id": "Z1", "metric": "ec",
        "ts": ago(1), "value": 9.99, "quality": "OK",
    }
    with migrated_db.begin() as connection:
        connection.execute(insert(Reading).values(row))
    assert api.get("/api/zones/Z1").json()["readings"]["ec"] is None
    assert [d["lastSeen"] for d in api.get("/api/devices").json()["items"]] == [None] * 5


def test_the_site_is_part_of_the_scope_as_well_as_the_org(tmp_path, api, migrated_db):
    """One organisation with two sites: each sees only its own, with the same IDs in both."""
    seed(migrated_db, write_pack(tmp_path / "second-site" / ORG, LAYOUT | {"site": "another-site"}))
    moment = contract_time(ago(1))
    readings = zone_telemetry()["readings"] | {"ec": 9.99}
    store(migrated_db, zone_telemetry(site="another-site", ts=moment, readings=readings))
    store(migrated_db, heartbeat(site="another-site", ts=moment))

    assert api.get("/api/zones/Z1").json()["readings"]["ec"] is None
    assert api.get("/api/zones/Z1").json()["lastSeen"] is None
    assert [zone["zone"] for zone in api.get("/api/zones").json()["items"]] == ["Z1", "Z2"]
    assert [d["lastSeen"] for d in api.get("/api/devices").json()["items"]] == [None] * 5


def test_a_zone_of_another_site_is_404(tmp_path, api, migrated_db):
    layout = copy.deepcopy(LAYOUT) | {"site": "another-site"}
    layout["zones"] = [layout_zone(1), layout_zone(2), layout_zone(7)]
    layout["devices"] = layout["devices"] + [{"id": "ZC7", "kind": "ZONE_CONTROLLER", "zone": "Z7"}]
    seed(migrated_db, write_pack(tmp_path / "client_B", layout))
    assert_error(api.get("/api/zones/Z7"), 404, "NOT_FOUND")


# --- When the database can't answer ------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/zones", "/api/zones/Z1", "/api/devices"])
def test_a_database_thats_down_is_503_in_the_contract_shape(pack, path, caplog):
    settings = dataclasses.replace(load_settings(), client_dir=pack, db_host="127.0.0.1", db_port=1)
    engine = _engine_for(settings)
    error = assert_error(api_client(pack, engine).get(path), 503, "SERVICE_UNAVAILABLE")
    assert "127.0.0.1" not in json.dumps(error)
    assert any("database not ready" in m for m in caplog.messages)


@pytest.mark.parametrize("path", ["/api/zones", "/api/zones/Z1", "/api/devices"])
def test_a_database_not_migrated_yet_is_503(empty_db, pack, path):
    error = assert_error(api_client(pack, empty_db).get(path), 503, "SERVICE_UNAVAILABLE")
    assert "relation" not in json.dumps(error)


@pytest.mark.parametrize("path", ["/api/zones", "/api/zones/Z1", "/api/devices"])
def test_a_farm_not_seeded_yet_is_503_not_an_empty_answer(migrated_db, pack, path):
    error = assert_error(api_client(pack, migrated_db).get(path), 503, "SERVICE_UNAVAILABLE")
    assert "not been loaded" in error["message"]


@pytest.mark.parametrize("path", ["/api/zones", "/api/zones/Z1", "/api/devices"])
def test_a_broken_pack_is_500_for_the_database_routes_too(pack, migrated_db, path):
    (pack / "layout.json").unlink()
    assert_error(api_client(pack, migrated_db).get(path), 500, "INTERNAL")


def test_a_device_with_no_row_in_the_layout_doesnt_appear(api, migrated_db):
    store(migrated_db, heartbeat("ZC9", ts=contract_time(ago(1))))
    assert "ZC9" not in [d["deviceId"] for d in api.get("/api/devices").json()["items"]]


# --- One error shape ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/nothing-here", "/nothing-at-all", "/api/zones/Z1/nothing"])
def test_an_unknown_path_is_404_in_the_contract_shape(api, path):
    assert_error(api.get(path), 404, "NOT_FOUND")


def test_a_wrong_method_is_405_in_the_contract_shape_and_says_what_is_allowed(api):
    response = api.post("/api/zones")
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET"


def test_an_unexpected_error_is_500_in_the_contract_shape_with_nothing_private(pack):
    def broken():
        raise RuntimeError("password=secret at C:\\private")

    client = api_client(pack, None)
    app.dependency_overrides[get_engine] = broken
    error = assert_error(client.get("/api/zones"), 500, "INTERNAL")
    assert "secret" not in json.dumps(error)
    assert "private" not in json.dumps(error)


def test_an_answer_that_breaks_its_own_model_is_500_not_a_broken_body(api, migrated_db):
    def broken(*args):
        raise ValueError("readings that break their model")

    from app.api import routes

    original, routes.zone_status = routes.zone_status, broken
    try:
        assert_error(api.get("/api/zones/Z1"), 500, "INTERNAL")
    finally:
        routes.zone_status = original


# --- What was already there still answers as it did ----------------------------------------


@pytest.mark.parametrize(
    ("database", "broker", "status", "overall"),
    [("UP", "UP", 200, "OK"), ("DOWN", "UP", 503, "DEGRADED"), ("UP", "DOWN", 503, "DEGRADED")],
)
def test_health_is_unchanged(monkeypatch, database, broker, status, overall):
    from app import main

    async def fake_database(settings):
        return database

    async def fake_broker(settings):
        return broker

    monkeypatch.setattr(main, "check_database", fake_database)
    monkeypatch.setattr(main, "check_broker", fake_broker)
    response = TestClient(app).get("/api/health")
    assert response.status_code == status
    assert response.json() == {"status": overall, "database": database, "broker": broker}


def test_the_docs_and_the_schema_still_answer():
    client = TestClient(app)
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_the_schema_lists_the_routes_with_the_contract_error_shape():
    schema = TestClient(app).get("/api/openapi.json").json()
    assert {"/api/health", "/api/farm", "/api/zones", "/api/zones/{zoneId}", "/api/devices"} <= set(schema["paths"])
    zone = schema["paths"]["/api/zones/{zoneId}"]["get"]
    assert zone["parameters"][0]["name"] == "zoneId"
    for route in (zone, schema["paths"]["/api/devices"]["get"]):
        assert route["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    assert zone["responses"]["404"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_the_schema_uses_the_wire_names():
    properties = TestClient(app).get("/api/openapi.json").json()["components"]["schemas"]["ZoneStatus"]["properties"]
    assert {"lastSeen", "activeCmdId", "configVersion"} <= set(properties)
    assert "last_seen" not in properties


def test_the_worker_and_the_contract_tools_dont_need_the_read_api():
    """Nothing the worker imports should pull in FastAPI's routes or read a config pack."""
    import subprocess
    import sys

    code = "import app.worker.main, sys; print('app.api.routes' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], check=True,
    )
    assert done.stdout.strip() == "False"


def test_logging_stays_quiet_for_a_normal_request(api, migrated_db, caplog):
    store(migrated_db, fresh_zone())
    with caplog.at_level(logging.WARNING):
        assert api.get("/api/zones").status_code == 200
        assert api.get("/api/devices").status_code == 200
    assert [r for r in caplog.records if r.name == "api"] == []


