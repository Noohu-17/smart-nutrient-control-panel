"""GET /api/commands/{cmdId}, POST /api/stop and POST /api/resume (T1-17, contract §7.4, §7.8, §11.2).

The routes are tested with a fake publisher that records what would go to the broker;
test_commands_broker.py sends real commands to a real broker.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from test_read_api import LAYOUT, ago, api_client, write_pack
from test_worker import heartbeat

from app.api.commands.publish import NotSent
from app.api.commands.routes import get_publisher
from app.api.schemas import contract_time
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker

ORG, SITE = "client_A", LAYOUT["site"]
CMD_ID = "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44"
# issuedBy names the signed-in caller (§7.1): "user:test-user" from conftest.sign_in_as.
ENVELOPE_KEYS = {
    "v", "type", "msgId", "ts", "site", "device", "cmdId", "kind", "issuedAt", "expiresAt", "issuedBy", "target",
}


class FakePublisher:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []
        self.broker_down = False

    def publish(self, topic, body):
        if self.broker_down:
            raise NotSent("cannot reach the broker")
        self.sent.append((topic, json.loads(json.dumps(body))))  # as it would go on the wire


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
def no_database():
    """An engine that fails if anything tries to use it."""
    return create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})


def client_for(pack, engine, publisher):
    client = api_client(pack, engine)
    app.dependency_overrides[get_publisher] = lambda: publisher
    return client


@pytest.fixture
def api(seeded, pack, publisher):
    return client_for(pack, seeded, publisher)


def handle(worker, message, topic=f"farm/{SITE}/zone/Z1/cmd/state"):
    asyncio.run(worker.handle(topic, json.dumps(message).encode()))


def cmd_state(state="EXECUTING", seq=10, **changes):
    """A CMD_STATE for a dose, as the simulator's zone controller sends it (§6.6, T4-8)."""
    return {
        "v": 1, "type": "CMD_STATE", "msgId": str(uuid.uuid4()), "ts": contract_time(ago(5)),
        "site": SITE, "device": "ZC1", "bootId": 4, "seq": seq,
        "cmdId": CMD_ID, "state": state, "reason": None,
        "lines": [
            {"line": "A", "requestedMl": 120, "deliveredMl": 64, "state": "RUNNING"},
            {"line": "B", "requestedMl": 120, "deliveredMl": 0, "state": "PENDING"},
        ],
        "before": {"ec": 1.51, "ph": 6.21}, "after": None,
        "startedAt": "2026-11-09T04:55:01.000Z", "endedAt": None,
    } | changes


def stopped_report(seq=12):
    lines = [
        {"line": "A", "requestedMl": 120, "deliveredMl": 64, "state": "SHORT"},
        {"line": "B", "requestedMl": 120, "deliveredMl": 0, "state": "SKIPPED"},
    ]
    return cmd_state(
        "STOPPED", seq, reason="STOP_REQUESTED", lines=lines, endedAt="2026-11-09T04:55:40.000Z"
    )


def parse_time(text):
    return datetime.fromisoformat(text)


def error_code(response):
    return response.json()["error"]["code"]


# --- GET /api/commands/{cmdId} ------------------------------------------------------------


def test_a_command_answers_as_its_controller_last_reported_it(api, worker):
    handle(worker, cmd_state("ACCEPTED", 10, lines=None, before=None, startedAt=None))
    handle(worker, cmd_state("EXECUTING", 11))
    handle(worker, stopped_report(12))

    response = api.get(f"/api/commands/{CMD_ID}")

    assert response.status_code == 200
    assert response.json() == {
        "cmdId": CMD_ID,
        "device": "ZC1",
        "state": "STOPPED",
        "reason": "STOP_REQUESTED",
        "lines": [
            {"line": "A", "requestedMl": 120, "deliveredMl": 64, "state": "SHORT"},
            {"line": "B", "requestedMl": 120, "deliveredMl": 0, "state": "SKIPPED"},
        ],
        "before": {"ec": 1.51, "ph": 6.21},
        "after": None,
        "startedAt": "2026-11-09T04:55:01.000Z",
        "endedAt": "2026-11-09T04:55:40.000Z",
    }


def test_an_older_report_arriving_late_never_replaces_a_newer_one(api, worker):
    handle(worker, cmd_state("COMPLETED", 20, lines=None, endedAt="2026-11-09T04:56:00.000Z"))
    handle(worker, cmd_state("EXECUTING", 19))
    assert api.get(f"/api/commands/{CMD_ID}").json()["state"] == "COMPLETED"


def test_the_report_after_the_mix_wait_fills_in_after(api, worker):
    done = {"lines": None, "endedAt": "2026-11-09T04:56:00.000Z"}
    handle(worker, cmd_state("COMPLETED", 20, **done))
    assert api.get(f"/api/commands/{CMD_ID}").json()["after"] is None
    handle(worker, cmd_state("COMPLETED", 21, after={"ec": 1.63, "ph": 6.02}, **done))
    assert api.get(f"/api/commands/{CMD_ID}").json()["after"] == {"ec": 1.63, "ph": 6.02}


def test_a_command_with_no_run_phase_has_no_lines(api, worker):
    handle(worker, cmd_state("COMPLETED", 30, lines=None, before=None, startedAt=None))
    body = api.get(f"/api/commands/{CMD_ID}").json()
    assert (body["state"], body["lines"], body["before"]) == ("COMPLETED", None, None)


def test_the_cmd_id_may_be_written_in_capitals(api, worker):
    handle(worker, cmd_state())
    assert api.get(f"/api/commands/{CMD_ID.upper()}").json()["cmdId"] == CMD_ID


@pytest.mark.parametrize("cmd_id", [str(uuid.uuid4()), "not-a-command", "6f1c2a90"])
def test_a_command_with_no_report_is_404(api, worker, cmd_id):
    handle(worker, cmd_state())
    response = api.get(f"/api/commands/{cmd_id}")
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"


def test_another_sites_command_never_shows(api, seeded, tmp_path):
    seed(seeded, write_pack(tmp_path / "second" / ORG, LAYOUT | {"site": "another-site"}))
    other = Worker(seeded, ORG, "another-site")
    handle(other, cmd_state(site="another-site"), "farm/another-site/zone/Z1/cmd/state")
    assert api.get(f"/api/commands/{CMD_ID}").status_code == 404


def test_a_database_that_cant_answer_is_503(pack, no_database, publisher):
    response = client_for(pack, no_database, publisher).get(f"/api/commands/{CMD_ID}")
    assert response.status_code == 503
    assert error_code(response) == "SERVICE_UNAVAILABLE"


# --- POST /api/stop -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "target"),
    [
        ({"scope": "ALL_OPERATIONS"}, {"scope": "ALL_OPERATIONS"}),
        ({"scope": "ZONE", "zone": "Z2"}, {"scope": "ZONE", "zone": "Z2"}),
    ],
    ids=["all-operations", "zone"],
)
def test_stop_publishes_a_stop_and_answers_202(api, publisher, body, target):
    before = datetime.now(UTC) - timedelta(milliseconds=1)
    response = api.post("/api/stop", json=body)

    assert response.status_code == 202
    answer = response.json()
    assert answer == {"cmdId": answer["cmdId"], "state": "DISPATCHED"}
    [(topic, command)] = publisher.sent
    assert topic == f"farm/{SITE}/stop"
    assert set(command) == ENVELOPE_KEYS
    assert command["issuedBy"] == "user:test-user"
    assert command | {"msgId": None, "ts": None, "issuedAt": None, "expiresAt": None} == {
        "v": 1, "type": "CMD", "msgId": None, "ts": None, "site": SITE, "device": "CLOUD",
        "cmdId": answer["cmdId"], "kind": "STOP", "issuedAt": None, "expiresAt": None,
        "issuedBy": "user:test-user", "target": target,
    }
    issued = parse_time(command["issuedAt"])
    assert before <= issued <= datetime.now(UTC)
    assert command["ts"] == command["issuedAt"]
    assert parse_time(command["expiresAt"]) - issued == timedelta(minutes=5)  # §7.4
    assert uuid.UUID(command["cmdId"]).version == 4 and uuid.UUID(command["msgId"]).version == 4
    assert command["msgId"] != command["cmdId"]


def test_pressing_stop_again_sends_a_new_command(api, publisher):
    first = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}).json()["cmdId"]
    second = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}).json()["cmdId"]
    assert first != second
    assert [command["cmdId"] for _, command in publisher.sent] == [first, second]


def test_stop_needs_no_database(pack, no_database, publisher):
    response = client_for(pack, no_database, publisher).post("/api/stop", json={"scope": "ZONE", "zone": "Z1"})
    assert response.status_code == 202
    assert len(publisher.sent) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"scope": "ZONE"},
        {"scope": "ALL_OPERATIONS", "zone": "Z1"},
        {"scope": "ALL_EQUIPMENT"},
        {"scope": "zone", "zone": "Z1"},
        {"scope": "ZONE", "zone": 1},
    ],
    ids=["empty", "zone-missing", "zone-with-all", "all-equipment", "lowercase", "zone-number"],
)
def test_a_stop_that_isnt_one_is_422_and_nothing_is_sent(api, publisher, body):
    response = api.post("/api/stop", json=body)
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"
    assert publisher.sent == []


def test_stop_for_a_zone_the_farm_hasnt_got_is_404(api, publisher):
    response = api.post("/api/stop", json={"scope": "ZONE", "zone": "Z9"})
    assert response.status_code == 404
    assert error_code(response) == "NOT_FOUND"
    assert publisher.sent == []


def test_stop_with_the_broker_down_is_503(api, publisher):
    publisher.broker_down = True
    response = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"})
    assert response.status_code == 503
    assert error_code(response) == "SERVICE_UNAVAILABLE"


def test_a_stop_can_be_followed_once_its_controller_reports(api, worker, publisher):
    cmd_id = api.post("/api/stop", json={"scope": "ZONE", "zone": "Z1"}).json()["cmdId"]
    assert api.get(f"/api/commands/{cmd_id}").json()["state"] == "DISPATCHED"  # nothing reported yet
    for seq, state in ((40, "ACCEPTED"), (41, "COMPLETED")):
        handle(worker, cmd_state(state, seq, cmdId=cmd_id, lines=None, before=None, startedAt=None))
    assert api.get(f"/api/commands/{cmd_id}").json()["state"] == "COMPLETED"


# --- POST /api/resume ---------------------------------------------------------------------


def online(worker, device="ZC1", topic="farm/siet-ph1/device/ZC1/status"):
    handle(worker, heartbeat(device, ts=contract_time(ago(5))), topic)


@pytest.mark.parametrize(
    ("body", "device", "topic", "target"),
    [
        ({"zone": "Z1"}, "ZC1", f"farm/{SITE}/zone/Z1/cmd", {"zone": "Z1"}),
        ({"zone": "Z2"}, "ZC2", f"farm/{SITE}/zone/Z2/cmd", {"zone": "Z2"}),
        ({"scope": "CLIMATE"}, "CC1", f"farm/{SITE}/climate/cmd", {"scope": "CLIMATE"}),
    ],
    ids=["zone-1", "zone-2", "climate"],
)
def test_resume_publishes_a_resume_to_its_controller(api, worker, publisher, body, device, topic, target):
    online(worker, device, f"farm/{SITE}/device/{device}/status")
    response = api.post("/api/resume", json=body)

    assert response.status_code == 202
    answer = response.json()
    assert answer == {"cmdId": answer["cmdId"], "state": "DISPATCHED"}
    [(sent_on, command)] = publisher.sent
    assert sent_on == topic
    assert set(command) == ENVELOPE_KEYS
    assert command["issuedBy"] == "user:test-user"
    assert (command["type"], command["kind"], command["device"]) == ("CMD", "RESUME", "CLOUD")
    assert (command["cmdId"], command["site"], command["target"]) == (answer["cmdId"], SITE, target)
    assert parse_time(command["expiresAt"]) - parse_time(command["issuedAt"]) == timedelta(seconds=60)


@pytest.mark.parametrize(
    "before",
    [
        lambda worker: None,  # never heard from
        lambda worker: handle(worker, heartbeat(ts=contract_time(ago(200))), f"farm/{SITE}/device/ZC1/status"),
        lambda worker: (
            online(worker),
            handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False}, f"farm/{SITE}/device/ZC1/status"),
        ),
    ],
    ids=["never-heard-from", "silent-3-minutes", "last-will"],
)
def test_resume_for_an_offline_controller_is_409_and_nothing_is_sent(api, worker, publisher, before):
    before(worker)
    response = api.post("/api/resume", json={"zone": "Z1"})
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "DEVICE_OFFLINE", "message": "ZC1 is offline, so nothing was sent"}
    assert publisher.sent == []


def test_another_controller_being_online_doesnt_count(api, worker, publisher):
    online(worker, "ZC2", f"farm/{SITE}/device/ZC2/status")
    assert api.post("/api/resume", json={"zone": "Z1"}).status_code == 409


@pytest.mark.parametrize(
    "body",
    [{}, {"zone": "Z1", "scope": "CLIMATE"}, {"scope": "ZONE"}, {"zone": None}],
    ids=["empty", "both", "scope-zone", "zone-null"],
)
def test_a_resume_that_isnt_one_is_422(api, worker, publisher, body):
    online(worker)
    response = api.post("/api/resume", json=body)
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"
    assert publisher.sent == []


def test_resume_for_a_zone_the_farm_hasnt_got_is_404(api, publisher):
    response = api.post("/api/resume", json={"zone": "Z9"})
    assert response.status_code == 404
    assert publisher.sent == []


def test_resume_with_the_database_down_is_503_and_nothing_is_sent(pack, no_database, publisher):
    response = client_for(pack, no_database, publisher).post("/api/resume", json={"zone": "Z1"})
    assert response.status_code == 503
    assert publisher.sent == []


def test_resume_before_the_farm_is_loaded_is_503(migrated_db, pack, publisher):
    response = client_for(pack, migrated_db, publisher).post("/api/resume", json={"zone": "Z1"})
    assert response.status_code == 503
    assert error_code(response) == "SERVICE_UNAVAILABLE"
    assert publisher.sent == []


def test_resume_with_the_broker_down_is_503(api, worker, publisher):
    online(worker)
    publisher.broker_down = True
    assert api.post("/api/resume", json={"zone": "Z1"}).status_code == 503


# --- The routes in the app ------------------------------------------------------------------


def test_the_routes_are_listed_in_the_api():
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", ())}
    assert {("/api/commands/{cmdId}", "GET"), ("/api/stop", "POST"), ("/api/resume", "POST")} <= routes


def test_one_publisher_per_process():
    from app.api.commands.publish import publisher_for
    from app.config import load_settings

    settings = load_settings()
    assert publisher_for(settings) is publisher_for(settings)
