"""GET /api/climate and GET /api/power (T1-21), in the shapes of contract §11.2 (T1-18).

Messages go in through the worker, as in test_latest_state, so these also show that the API
and the worker agree on what is kept.
"""

import asyncio
import copy
import dataclasses
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from test_read_api import CONTRACT, LAYOUT, ago, api_client, assert_error, write_pack
from test_worker import SIM_TS, climate_telemetry, envelope, heartbeat, power_state

from app.api.routes import _engine_for
from app.api.schemas import ClimateStatus, PowerStatus, contract_time
from app.api.status import FRESH_FOR, OFFLINE_AFTER, power_status
from app.config import load_settings
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker

ORG = "client_A"
SITE = LAYOUT["site"]
CLIMATE_TOPIC = f"farm/{SITE}/climate/telemetry"
POWER_TOPIC = f"farm/{SITE}/power/state"
UPS = {"onBattery": False, "batteryPct": 100, "runtimeMin": 42}
EQUIPMENT = {"fanStage": 2, "FAN1": "ON", "PAD1": "ON", "SHADE1": "OPEN", "CH1": {"state": "ON", "setpointC": 22.0}}
POWER_VALUES = ("l1V", "l2V", "l3V", "upsBatteryPct", "upsRuntimeMin")


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


def handle(worker, message, topic):
    asyncio.run(worker.handle(topic, json.dumps(message).encode()))


def now_ts(seconds_ago=1):
    return contract_time(ago(seconds_ago))


def climate_state(**changes):
    return envelope("CLIMATE_STATE", "CC1") | {
        "mode": "MANUAL", "manualUntil": "2026-11-09T05:54:00.000Z", "stopped": False,
        "equipment": EQUIPMENT, "latches": [],
    } | changes


def full_power_state(**changes):
    return power_state(generator="STOPPED", ups=UPS) | changes


def contract_example(heading: str) -> dict:
    text = CONTRACT.read_text(encoding="utf-8")
    after = text[text.index(heading):]
    return json.loads(re.search(r"```json\n(.*?)```", after, re.DOTALL).group(1))


def layout_with_power(**power) -> dict:
    layout = copy.deepcopy(LAYOUT)
    layout["power"] |= power
    return layout


# --- GET /api/climate --------------------------------------------------------------------


def test_climate_answers_with_its_latest_readings_and_state(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(2), seq=10), CLIMATE_TOPIC)
    handle(worker, climate_state(ts=now_ts(1), seq=11, latches=["CABINET_POWER_CUT"]), f"farm/{SITE}/climate/state")

    response = api.get("/api/climate")

    assert response.status_code == 200
    body = response.json()
    assert body["online"] is True
    assert body["lastSeen"] is not None and body["ts"] is not None
    assert body["readings"]["airTempC"] == 31.2
    assert body["readings"]["airTempByPosC"] == {"PAD_END": 29.4, "FAN_END": 33.1}
    assert body["quality"] == {"airTempC": "OK", "airRh": "OK"}
    assert (body["mode"], body["manualUntil"], body["stopped"]) == ("MANUAL", "2026-11-09T05:54:00.000Z", False)
    assert body["equipment"] == EQUIPMENT
    assert body["latches"] == ["CABINET_POWER_CUT"]


def test_climate_has_exactly_the_fields_of_the_contract_example(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(2)), CLIMATE_TOPIC)
    handle(worker, climate_state(ts=now_ts(1), seq=40), f"farm/{SITE}/climate/state")
    body = api.get("/api/climate").json()
    example = contract_example("**`GET /climate`**")
    assert list(body) == list(example)
    assert set(body["readings"]) == set(example["readings"])
    assert not {"msgId", "bootId", "seq"} & set(body)


def test_the_climate_example_goes_through_the_model_unchanged():
    example = contract_example("**`GET /climate`**")
    assert ClimateStatus.model_validate(example).model_dump(mode="json", exclude_unset=True) == example


def test_a_climate_controller_that_has_never_reported_answers_as_the_contract_says(api):
    body = api.get("/api/climate").json()
    assert body["online"] is False
    for field in ("lastSeen", "ts", "mode", "manualUntil", "stopped"):
        assert body[field] is None, field
    assert body["readings"]["airTempByPosC"] == {"PAD_END": None}  # keyed by the layout's airSensors
    assert all(v is None for k, v in body["readings"].items() if k != "airTempByPosC")
    assert (body["quality"], body["equipment"], body["latches"]) == ({}, {}, [])
    assert "simTs" not in body


def test_climate_state_is_null_until_a_climate_state_arrives(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(1), mode="MANUAL"), CLIMATE_TOPIC)
    body = api.get("/api/climate").json()
    assert body["readings"]["airTempC"] == 31.2
    assert (body["mode"], body["stopped"], body["equipment"]) == (None, None, {})  # from CLIMATE_STATE only


def test_an_older_climate_state_never_replaces_a_newer_one(api, worker):
    handle(worker, climate_state(ts=now_ts(1), seq=30, mode="AUTOMATIC", manualUntil=None), f"farm/{SITE}/climate/state")
    handle(worker, climate_state(ts=now_ts(1), seq=29, mode="MANUAL"), f"farm/{SITE}/climate/state")
    body = api.get("/api/climate").json()
    assert (body["mode"], body["manualUntil"]) == ("AUTOMATIC", None)


def test_the_newest_climate_telemetry_wins_whatever_order_it_came_in(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(5), seq=21, readings=climate_telemetry()["readings"] | {"airRh": 60}), CLIMATE_TOPIC)
    handle(worker, climate_telemetry(ts=now_ts(30), seq=20, readings=climate_telemetry()["readings"] | {"airRh": 90}), CLIMATE_TOPIC)
    assert api.get("/api/climate").json()["readings"]["airRh"] == 60


@pytest.mark.parametrize(
    ("age", "sent", "served"),
    [
        (5, "OK", "OK"),
        (FRESH_FOR.total_seconds() + 5, "OK", "STALE"),
        (FRESH_FOR.total_seconds() + 5, "FAULT", "FAULT"),
        (FRESH_FOR.total_seconds() + 5, "OUT_OF_RANGE", "OUT_OF_RANGE"),
    ],
)
def test_climate_ok_goes_stale_and_every_other_quality_stays(api, worker, age, sent, served):
    readings = climate_telemetry()["readings"] | ({"airTempC": None} if sent == "FAULT" else {})
    handle(worker, climate_telemetry(ts=now_ts(age), readings=readings, quality={"airTempC": sent}), CLIMATE_TOPIC)
    handle(worker, heartbeat("CC1", ts=now_ts(1), seq=99), f"farm/{SITE}/device/CC1/status")  # still online
    body = api.get("/api/climate").json()
    assert body["online"] is True
    assert body["quality"]["airTempC"] == served


def test_the_last_will_shows_the_climate_offline_at_once_with_its_last_values(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(2), seq=10), CLIMATE_TOPIC)
    handle(worker, climate_state(ts=now_ts(1), seq=11), f"farm/{SITE}/climate/state")
    assert api.get("/api/climate").json()["online"] is True

    handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "CC1", "online": False}, f"farm/{SITE}/device/CC1/status")

    body = api.get("/api/climate").json()
    assert body["online"] is False
    assert body["quality"] == {"airTempC": "STALE", "airRh": "STALE"}
    assert body["readings"]["airTempC"] == 31.2
    assert body["mode"] == "MANUAL"


def test_climate_goes_offline_after_three_minutes_of_silence(api, worker):
    old = OFFLINE_AFTER.total_seconds() + 5
    handle(worker, climate_telemetry(ts=now_ts(old)), CLIMATE_TOPIC)
    body = api.get("/api/climate").json()
    assert (body["online"], body["quality"]["airTempC"]) == (False, "STALE")


def test_climate_sim_ts_is_left_out_unless_the_reading_has_it(api, worker):
    handle(worker, climate_telemetry(ts=now_ts(2), seq=10), CLIMATE_TOPIC)
    assert "simTs" not in api.get("/api/climate").json()
    handle(worker, climate_telemetry(ts=now_ts(1), seq=11, simTs=SIM_TS), CLIMATE_TOPIC)
    assert api.get("/api/climate").json()["simTs"] == SIM_TS


# --- GET /api/power ----------------------------------------------------------------------


def test_power_answers_with_its_latest_power_state(api, worker):
    handle(worker, full_power_state(ts=now_ts(1)), POWER_TOPIC)
    body = api.get("/api/power").json()
    assert body == {
        "online": True,
        "lastSeen": body["lastSeen"],
        "ts": body["lastSeen"],
        "mains": "OK",
        "phases": {"l1V": 231, "l2V": None, "l3V": 233},
        "source": "MAINS",
        "generator": "STOPPED",
        "ats": "MAINS",
        "ups": UPS,
        "quality": {},
    }


def test_power_has_exactly_the_fields_of_the_contract_example(api, worker):
    handle(worker, full_power_state(ts=now_ts(1)), POWER_TOPIC)
    body = api.get("/api/power").json()
    assert list(body) == list(contract_example("**`GET /power`**"))


def test_the_power_example_goes_through_the_model_unchanged():
    example = contract_example("**`GET /power`**")
    assert PowerStatus.model_validate(example).model_dump(mode="json", exclude_unset=True) == example


def test_a_power_monitor_that_has_never_reported_answers_as_the_contract_says(api):
    body = api.get("/api/power").json()
    assert body == {
        "online": False, "lastSeen": None, "ts": None, "mains": None,
        "phases": {"l1V": None, "l2V": None, "l3V": None}, "source": None, "generator": None,
        "ats": None, "ups": None, "quality": {},
    }


@pytest.mark.parametrize(
    ("power", "absent"),
    [({"generator": False}, {"generator"}), ({"ups": False}, {"ups"}), ({"generator": False, "ups": False}, {"generator", "ups"})],
)
def test_no_generator_or_ups_field_on_a_site_without_one(tmp_path, migrated_db, power, absent):
    pack = write_pack(tmp_path / ORG, layout_with_power(**power))
    seed(migrated_db, pack)
    api = api_client(pack, migrated_db)
    assert absent.isdisjoint(api.get("/api/power").json())  # never reported

    message = power_state(ts=now_ts(1))
    if "generator" not in absent:
        message["generator"] = "RUNNING"
    if "ups" not in absent:
        message["ups"] = UPS
    handle(Worker(migrated_db, ORG, SITE), message, POWER_TOPIC)
    body = api.get("/api/power").json()
    assert absent.isdisjoint(body)
    assert body["mains"] == "OK"


def test_a_generator_or_ups_the_layout_has_but_the_message_left_out_is_null(api, worker):
    handle(worker, power_state(ts=now_ts(1)), POWER_TOPIC)
    body = api.get("/api/power").json()
    assert (body["generator"], body["ups"]) == (None, None)


def test_an_old_power_state_makes_every_value_stale(api, worker):
    handle(worker, full_power_state(ts=now_ts(FRESH_FOR.total_seconds() + 5)), POWER_TOPIC)
    handle(worker, heartbeat("PM1", ts=now_ts(1), seq=99), f"farm/{SITE}/device/PM1/status")
    body = api.get("/api/power").json()
    assert body["online"] is True
    assert body["quality"] == dict.fromkeys(POWER_VALUES, "STALE")
    assert body["phases"]["l1V"] == 231  # the values stay as sent


def test_the_last_will_shows_the_power_offline_at_once_with_its_last_values(api, worker):
    handle(worker, full_power_state(ts=now_ts(1)), POWER_TOPIC)
    handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "PM1", "online": False}, f"farm/{SITE}/device/PM1/status")
    body = api.get("/api/power").json()
    assert body["online"] is False
    assert body["quality"] == dict.fromkeys(POWER_VALUES, "STALE")
    assert (body["mains"], body["source"]) == ("OK", "MAINS")


def test_without_a_ups_the_stale_values_are_only_the_phases():
    latest = type("Row", (), {"ts": datetime(2026, 11, 9, 5, tzinfo=UTC), "sim_ts": None, "body": power_state()})
    status = power_status(False, False, None, latest, datetime(2026, 11, 9, 5, 1, tzinfo=UTC))
    assert status.quality == dict.fromkeys(("l1V", "l2V", "l3V"), "STALE")


def test_an_older_power_state_never_replaces_a_newer_one(api, worker):
    handle(worker, full_power_state(ts=now_ts(1), seq=50, mains="LOST", source="UPS_ONLY"), POWER_TOPIC)
    handle(worker, full_power_state(ts=now_ts(1), seq=49), POWER_TOPIC)
    body = api.get("/api/power").json()
    assert (body["mains"], body["source"]) == ("LOST", "UPS_ONLY")


def test_the_power_monitor_is_online_again_after_it_comes_back(api, worker):
    handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "PM1", "online": False}, f"farm/{SITE}/device/PM1/status")
    later = contract_time(datetime.now(UTC) + timedelta(milliseconds=20))
    handle(worker, full_power_state(ts=later, bootId=5, seq=1), POWER_TOPIC)
    body = api.get("/api/power").json()
    assert (body["online"], body["quality"]) == (True, {})


# --- Scope and errors, as for the zones ------------------------------------------------


def test_another_clients_climate_and_power_never_show(api, seeded, tmp_path):
    seed(seeded, write_pack(tmp_path / "client_B", LAYOUT | {"site": "another-site"}))
    other = Worker(seeded, "client_B", "another-site")
    handle(other, climate_telemetry(ts=now_ts(1), site="another-site"), "farm/another-site/climate/telemetry")
    handle(other, climate_state(ts=now_ts(1), site="another-site"), "farm/another-site/climate/state")
    handle(other, full_power_state(ts=now_ts(1), site="another-site"), "farm/another-site/power/state")
    climate, power = api.get("/api/climate").json(), api.get("/api/power").json()
    assert (climate["online"], climate["mode"], climate["readings"]["airTempC"]) == (False, None, None)
    assert (power["online"], power["mains"]) == (False, None)


@pytest.mark.parametrize("path", ["/api/climate", "/api/power"])
def test_a_database_thats_down_is_503_in_the_contract_shape(pack, path):
    settings = dataclasses.replace(load_settings(), client_dir=pack, db_host="127.0.0.1", db_port=1)
    error = assert_error(api_client(pack, _engine_for(settings)).get(path), 503, "SERVICE_UNAVAILABLE")
    assert "127.0.0.1" not in json.dumps(error)


@pytest.mark.parametrize("path", ["/api/climate", "/api/power"])
def test_a_database_not_migrated_yet_is_503(empty_db, pack, path):
    assert_error(api_client(pack, empty_db).get(path), 503, "SERVICE_UNAVAILABLE")


@pytest.mark.parametrize("path", ["/api/climate", "/api/power"])
def test_a_farm_not_seeded_yet_is_503_not_an_empty_answer(migrated_db, pack, path):
    error = assert_error(api_client(pack, migrated_db).get(path), 503, "SERVICE_UNAVAILABLE")
    assert "not been loaded" in error["message"]


@pytest.mark.parametrize("path", ["/api/climate", "/api/power"])
def test_a_broken_pack_is_500(pack, migrated_db, path):
    (pack / "layout.json").unlink()
    assert_error(api_client(pack, migrated_db).get(path), 500, "INTERNAL")


@pytest.mark.parametrize("path", ["/api/climate", "/api/power"])
def test_a_wrong_method_is_405_in_the_contract_shape(api, path):
    response = api.post(path)
    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET"


def test_the_schema_lists_both_routes_with_the_contract_error_shape():
    from fastapi.testclient import TestClient

    schema = TestClient(app).get("/api/openapi.json").json()
    for path in ("/api/climate", "/api/power"):
        route = schema["paths"][path]["get"]
        assert route["responses"]["503"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    assert {"manualUntil", "airTempByPosC"} <= set(schema["components"]["schemas"]["ClimateStatus"]["properties"]) | set(
        schema["components"]["schemas"]["ClimateReadings"]["properties"]
    )
