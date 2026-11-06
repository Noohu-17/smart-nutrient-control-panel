"""POST /api/dose-requests/{id}/confirm (T1-17 part 3, contract §11.1, §11.3) and the DISPATCHED
answer of GET /api/commands/{cmdId}.

The zone is set up through the worker and reviewed with POST /api/dose-requests, as in
test_dose_requests. Commands go to a fake publisher, as in test_commands.
"""

import json
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, select, update
from test_commands import FakePublisher, client_for, error_code
from test_dose_requests import (
    CLIENT_A,
    DOSE,
    ORG,
    SITE,
    cmd_state,
    handle,
    ready_zone,
    review,
    sim_ago,
    write_dosing_pack,
)
from test_read_api import CONTRACT

from app.api.commands.messages import COMMAND_EXPIRY
from app.api.commands.schemas import CommandStatus
from app.db.models import SentCommand
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker

LIMITS = json.loads((CLIENT_A / "limits.json").read_text(encoding="utf-8"))["zones"]["Z1"]
CMD_TOPIC = f"farm/{SITE}/zone/Z1/cmd"
CMD_STATE_TOPIC = f"farm/{SITE}/zone/Z1/cmd/state"


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pack(tmp_path):
    return write_dosing_pack(tmp_path / ORG)


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


def confirm(api, request_id, key=None):
    headers = {} if key is None else {"Idempotency-Key": str(key)}
    return api.post(f"/api/dose-requests/{request_id}/confirm", headers=headers)


def reviewed(api, worker, request=DOSE):
    ready_zone(worker)
    body = review(api, request)
    assert body["canConfirm"], body["checks"]
    return body["id"]


def saved_commands(engine):
    with engine.connect() as connection:
        return connection.execute(select(SentCommand)).all()


def parse_time(text):
    return datetime.fromisoformat(text)


# --- A dose that may go ahead --------------------------------------------------------------


def test_a_confirmed_dose_is_sent_and_answers_202(api, worker, publisher):
    response = confirm(api, reviewed(api, worker), uuid.uuid4())
    assert response.status_code == 202
    answer = response.json()
    assert answer["state"] == "DISPATCHED"
    assert str(uuid.UUID(answer["cmdId"])) == answer["cmdId"]
    [(topic, command)] = publisher.sent
    assert topic == CMD_TOPIC
    assert command["cmdId"] == answer["cmdId"]


def test_the_dose_command_is_the_one_in_the_contract(api, worker, publisher):
    confirm(api, reviewed(api, worker), uuid.uuid4())
    [(_, command)] = publisher.sent
    assert {key: command[key] for key in ("v", "type", "site", "device", "kind", "target")} == {
        "v": 1, "type": "CMD", "site": SITE, "device": "CLOUD", "kind": "DOSE", "target": {"zone": "Z1"},
    }
    assert command["lines"] == DOSE["lines"]
    assert command["order"] == ["A", "B", "ACID"]
    assert command["limits"] == LIMITS["limits"]  # the snapshot the review used (§7.2)
    for key in ("mixWaitS", "maxDurationS", "tolerancePct"):
        assert command[key] == LIMITS["dose"][key]  # from limits.json
    assert parse_time(command["expiresAt"]) - parse_time(command["issuedAt"]) == COMMAND_EXPIRY
    assert command["ts"] == command["issuedAt"]
    assert not {"issuedBy", "bootId", "seq"} & command.keys()


def change_limits(pack, change):
    limits = json.loads((pack / "limits.json").read_text(encoding="utf-8"))
    change(limits["zones"])
    (pack / "limits.json").write_text(json.dumps(limits), encoding="utf-8")


def test_the_run_settings_come_from_limits_json(api, worker, publisher, pack):
    change_limits(pack, lambda zones: zones["Z1"].update(dose={"mixWaitS": 120, "maxDurationS": 450, "tolerancePct": 7}))
    confirm(api, reviewed(api, worker), uuid.uuid4())
    [(_, command)] = publisher.sent
    assert (command["mixWaitS"], command["maxDurationS"], command["tolerancePct"]) == (120, 450, 7)


def test_the_command_carries_the_limits_the_review_used(api, worker, publisher, pack):
    request_id = reviewed(api, worker)
    change_limits(pack, lambda zones: zones["Z1"]["limits"]["maxPerDoseMl"].update(A=250))  # still above 120
    confirm(api, request_id, uuid.uuid4())
    [(_, command)] = publisher.sent
    assert command["limits"] == LIMITS["limits"]


def test_lines_the_zone_no_longer_has_are_422(api, worker, publisher, pack):
    request_id = reviewed(api, worker)
    layout = json.loads((pack / "layout.json").read_text(encoding="utf-8"))
    layout["zones"][0]["doseLines"] = ["A", "B"]
    (pack / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    response = confirm(api, request_id, uuid.uuid4())
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "INVALID_COMMAND", "message": "Zone 1 has no dosing line ACID"}
    assert publisher.sent == []


def test_a_zone_no_longer_in_the_limits_is_404(api, worker, publisher, pack):
    request_id = reviewed(api, worker)
    change_limits(pack, lambda zones: zones.pop("Z1"))
    response = confirm(api, request_id, uuid.uuid4())
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "There is no zone Z1"}
    assert publisher.sent == []


def test_lines_run_in_the_layouts_order(api, worker, publisher):
    request = {"zone": "Z1", "lines": [{"line": "ACID", "ml": 20}, {"line": "A", "ml": 50}]}
    confirm(api, reviewed(api, worker, request), uuid.uuid4())
    [(_, command)] = publisher.sent
    assert command["order"] == ["A", "ACID"]
    assert command["lines"] == request["lines"]


def test_the_command_is_saved_with_its_request_and_key(api, worker, seeded):
    request_id, key = reviewed(api, worker), str(uuid.uuid4())
    cmd_id = confirm(api, request_id, key).json()["cmdId"]
    [saved] = saved_commands(seeded)
    assert (saved.cmd_id, saved.dose_request_id, saved.idempotency_key, saved.kind) == (
        cmd_id, request_id, key, "DOSE",
    )
    assert saved.topic == CMD_TOPIC and saved.body["cmdId"] == cmd_id
    assert saved.dispatched_at is not None


# --- Once only (§11.1) ---------------------------------------------------------------------


def test_the_same_key_again_gets_the_same_cmd_id_and_sends_nothing(api, worker, publisher):
    request_id, key = reviewed(api, worker), uuid.uuid4()
    first = confirm(api, request_id, key)
    again = confirm(api, request_id, key)
    assert again.status_code == 202
    assert again.json() == first.json()
    assert len(publisher.sent) == 1


@pytest.mark.parametrize("second_key", [uuid.uuid4(), None], ids=["another-key", "no-key"])
def test_another_confirm_of_a_confirmed_request_is_409(api, worker, publisher, second_key):
    request_id = reviewed(api, worker)
    cmd_id = confirm(api, request_id, uuid.uuid4()).json()["cmdId"]
    response = confirm(api, request_id, second_key)
    assert response.status_code == 409
    assert error_code(response) == "CONFLICT"
    assert cmd_id in response.json()["error"]["message"]
    assert len(publisher.sent) == 1


def test_without_a_key_a_request_is_still_confirmed_once(api, worker, publisher):
    request_id = reviewed(api, worker)
    assert confirm(api, request_id).status_code == 202
    assert confirm(api, request_id).status_code == 409
    assert len(publisher.sent) == 1


def test_a_key_used_for_another_dose_request_is_409(api, worker, publisher):
    key = uuid.uuid4()
    confirm(api, reviewed(api, worker), key)
    other = review(api)["id"]
    response = confirm(api, other, key)
    assert response.status_code == 409
    assert error_code(response) == "CONFLICT"
    assert len(publisher.sent) == 1
    assert confirm(api, other, uuid.uuid4()).status_code == 202  # nothing was kept for it


def test_two_confirms_at_once_send_one_command(seeded, pack, worker):
    slow = FakePublisher()
    real_publish = slow.publish

    def publish(topic, body):
        time.sleep(0.5)  # the second confirm arrives while the first is still sending
        real_publish(topic, body)

    slow.publish = publish
    clients = [client_for(pack, seeded, slow) for _ in range(2)]
    request_id, key = reviewed(clients[0], worker), uuid.uuid4()
    answers = []
    threads = [threading.Thread(target=lambda c=c: answers.append(confirm(c, request_id, key))) for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [a.status_code for a in answers] == [202, 202]
    assert answers[0].json() == answers[1].json()
    assert len(slow.sent) == 1


# --- Checked again ---------------------------------------------------------------------


def test_a_review_that_blocked_is_409_with_its_check(api, worker, publisher):
    ready_zone(worker, state={"estop": True})
    body = review(api)
    response = confirm(api, body["id"], uuid.uuid4())
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "ESTOP", "message": "E-STOP pressed in Zone 1"}
    ready_zone(worker, seq=20)  # released since: still not sent, as the person saw it blocked
    assert error_code(confirm(api, body["id"], uuid.uuid4())) == "ESTOP"
    assert publisher.sent == []


@pytest.mark.parametrize(
    ("state", "code", "message"),
    [
        ({"estop": True}, "ESTOP", "E-STOP pressed in Zone 1"),
        ({"stopped": True}, "STATE_KNOWN", "Zone 1 is stopped. Resume the zone first"),
        ({"mode": "LOCAL"}, "ZONE_REMOTE", "Zone 1 key switch is in LOCAL"),
    ],
)
def test_the_dose_is_checked_again_with_the_zone_as_it_is_now(api, worker, publisher, seeded, state, code, message):
    request_id = reviewed(api, worker)
    ready_zone(worker, state=state, seq=20)  # changed since the review
    response = confirm(api, request_id, uuid.uuid4())
    assert response.status_code == 409
    assert response.json()["error"] == {"code": code, "message": message}
    assert publisher.sent == [] and saved_commands(seeded) == []
    ready_zone(worker, seq=30)  # back as it was: the same review may go ahead
    assert confirm(api, request_id, uuid.uuid4()).status_code == 202


def test_a_dose_delivered_since_the_review_counts_for_the_24h_limit(api, worker, publisher):
    request_id = reviewed(api, worker)
    handle(worker, cmd_state({"A": 1400}, sim_ts=sim_ago(1)), CMD_STATE_TOPIC)
    response = confirm(api, request_id, uuid.uuid4())
    assert response.json()["error"] == {
        "code": "LIMIT_24H", "message": "Line A would reach 1520 mL of the 1500 mL 24 h limit",
    }
    assert publisher.sent == []


def test_an_unknown_dose_request_is_404(api, worker, publisher):
    response = confirm(api, "dr_0000", uuid.uuid4())
    assert response.status_code == 404
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "There is no dose request dr_0000"}
    assert publisher.sent == []


@pytest.mark.parametrize("key", ["not-a-uuid", "6f1c2a90"])
def test_a_key_that_isnt_a_uuid_is_422(api, worker, publisher, key):
    response = confirm(api, reviewed(api, worker), key)
    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"
    assert [f["field"] for f in response.json()["error"]["details"]["fields"]] == ["header.Idempotency-Key"]
    assert publisher.sent == []


def test_a_database_that_cant_answer_is_503(pack, publisher):
    nothing_there = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    response = confirm(client_for(pack, nothing_there, publisher), "dr_1", uuid.uuid4())
    assert response.status_code == 503
    assert publisher.sent == []


# --- When the broker doesn't take it ---------------------------------------------------


def test_with_the_broker_down_it_is_503_and_the_same_key_sends_the_same_command(api, worker, publisher, seeded):
    request_id, key = reviewed(api, worker), uuid.uuid4()
    publisher.broker_down = True
    response = confirm(api, request_id, key)
    assert response.status_code == 503
    assert error_code(response) == "SERVICE_UNAVAILABLE"
    [saved] = saved_commands(seeded)
    assert saved.dispatched_at is None
    assert api.get(f"/api/commands/{saved.cmd_id}").status_code == 404  # not sent, so not DISPATCHED

    publisher.broker_down = False
    again = confirm(api, request_id, key)
    assert again.status_code == 202
    assert again.json()["cmdId"] == saved.cmd_id
    [(_, command)] = publisher.sent
    assert command == saved.body  # the same command, msgId and expiry included


def test_after_a_503_another_key_is_still_409(api, worker, publisher):
    request_id = reviewed(api, worker)
    publisher.broker_down = True
    confirm(api, request_id, uuid.uuid4())
    publisher.broker_down = False
    assert confirm(api, request_id, uuid.uuid4()).status_code == 409
    assert publisher.sent == []


def test_a_command_that_expired_before_the_broker_took_it_is_never_sent(api, worker, publisher, seeded):
    request_id, key = reviewed(api, worker), uuid.uuid4()
    publisher.broker_down = True
    confirm(api, request_id, key)
    with seeded.begin() as connection:
        connection.execute(update(SentCommand).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    publisher.broker_down = False
    response = confirm(api, request_id, key)
    assert response.status_code == 409
    assert "Review the dose again" in response.json()["error"]["message"]
    assert publisher.sent == []


# --- GET /api/commands/{cmdId} -------------------------------------------------------------


def test_get_answers_dispatched_until_the_first_cmd_state(api, worker):
    cmd_id = confirm(api, reviewed(api, worker), uuid.uuid4()).json()["cmdId"]
    response = api.get(f"/api/commands/{cmd_id}")
    assert response.status_code == 200
    assert response.json() == {
        "cmdId": cmd_id, "device": None, "state": "DISPATCHED", "reason": None, "lines": None,
        "before": None, "after": None, "startedAt": None, "endedAt": None,
    }
    handle(worker, cmd_state({"A": 0}, cmd_id=cmd_id, state="ACCEPTED"), CMD_STATE_TOPIC)
    answer = api.get(f"/api/commands/{cmd_id}").json()
    assert (answer["state"], answer["device"]) == ("ACCEPTED", "ZC1")


def test_a_sent_resume_answers_dispatched_too(api, worker):
    ready_zone(worker)  # ZC1 heard from just now, so online
    cmd_id = api.post("/api/resume", json={"zone": "Z1"}).json()["cmdId"]
    assert api.get(f"/api/commands/{cmd_id}").json()["state"] == "DISPATCHED"


def test_another_sites_sent_command_never_shows(api, seeded):
    cmd_id, now = str(uuid.uuid4()), datetime.now(UTC)
    with seeded.begin() as connection:
        connection.execute(
            insert(SentCommand).values(
                org_id=ORG, site_id="another-site", cmd_id=cmd_id, kind="STOP", topic="farm/another-site/stop",
                body={}, issued_at=now, expires_at=now, dispatched_at=now,
            )
        )
    assert api.get(f"/api/commands/{cmd_id}").status_code == 404


def test_the_route_is_in_the_api_with_its_key_header():
    operation = app.openapi()["paths"]["/api/dose-requests/{id}/confirm"]["post"]
    headers = [p["name"] for p in operation["parameters"] if p["in"] == "header"]
    assert headers == ["Idempotency-Key"]
    assert {"202", "404", "409", "422", "503"} <= operation["responses"].keys()


def test_the_contract_example_is_the_answers_shape():
    text = CONTRACT.read_text(encoding="utf-8")
    section = text[text.index("**`GET /commands/{cmdId}`**"):text.index("### 11.3")]
    example = json.loads(re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1))
    answer = CommandStatus.model_validate(example)
    assert answer.model_dump(mode="json", by_alias=True) == example
    assert example.keys() == CommandStatus.dispatched(example["cmdId"]).model_dump(by_alias=True).keys()
