"""GET /api/operations (T1-28, contract §11.2): every dose, stop, resume and acknowledgement, newest
first, with who did it, when and what came of it.

Each action is done through the API as a person would, as in test_dose_confirm, test_commands and
test_alerts: the dose is reviewed and confirmed, its controller reports it through the worker, the
stop and the resume go to a fake publisher, and the alert is acknowledged.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert, update
from test_commands import FakePublisher, client_for, online
from test_dose_confirm import CMD_STATE_TOPIC, confirm, reviewed
from test_dose_requests import DOSE, ORG, SITE, cmd_state, handle, write_dosing_pack
from test_read_api import LAYOUT, assert_error

from app.db.models import Alert, SentCommand, User
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker
from conftest import sign_in_as

PEOPLE = {
    "op-1": ("Ravi Operator", "OPERATOR"),
    "vw-1": ("Vani Viewer", "VIEWER"),
    "ag-1": ("Asha Agronomist", "AGRONOMIST"),
    "ow-1": ("Farm Owner", "OWNER"),
}


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
    with migrated_db.begin() as connection:
        connection.execute(insert(User), [
            {"org_id": ORG, "site_id": SITE, "user_id": user_id, "name": name, "email": f"{user_id}@example.com",
             "role": role}
            for user_id, (name, role) in PEOPLE.items()
        ])
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
def no_database():
    """An engine that fails if anything tries to use it, as in test_commands."""
    return create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})


def as_person(user_id):
    sign_in_as(app, PEOPLE[user_id][1], user_id=user_id)


def operations(api, **query):
    response = api.get("/api/operations", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def dose(api, worker, *, before=None, after=None):
    """Review and confirm the dose, and its controller reports it COMPLETED. Returns its cmdId."""
    cmd_id = confirm(api, reviewed(api, worker), uuid.uuid4()).json()["cmdId"]
    delivered = {line["line"]: line["ml"] for line in DOSE["lines"]}
    handle(worker, cmd_state(delivered, cmd_id=cmd_id, before=before, after=after), CMD_STATE_TOPIC)
    return cmd_id


def stop(api, body=None):
    response = api.post("/api/stop", json=body or {"scope": "ALL_OPERATIONS"})
    assert response.status_code == 202, response.text
    return response.json()["cmdId"]


def resume(api, worker, zone="Z1"):
    online(worker)
    response = api.post("/api/resume", json={"zone": zone})
    assert response.status_code == 202, response.text
    return response.json()["cmdId"]


def raised_alert(engine, zone="Z1", kind="EC_HIGH"):
    alert_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(insert(Alert).values(
            org_id=ORG, site_id=SITE, alert_id=alert_id, key=f"{kind}:{zone}:{alert_id}", kind=kind,
            severity="CRITICAL", zone_id=zone, message="EC in Zone A is 2.6 mS/cm, above its alarm limit",
            system_action="No automatic action", next_step="Check the dosing", first_seen_at=now, raised_at=now,
        ))
    return alert_id


def ack(api, engine, zone="Z1"):
    alert_id = raised_alert(engine, zone)
    assert api.post(f"/api/alerts/{alert_id}/ack").status_code == 200
    return alert_id


# --- Done when: after a dose, stop, resume and ack, all are listed with who did them -------


def test_a_dose_a_stop_a_resume_and_an_ack_are_listed_newest_first_with_who_did_them(api, worker, seeded):
    as_person("op-1")
    dosed = dose(api, worker, before={"ec": 1.51, "ph": 6.21}, after={"ec": 1.63, "ph": 6.02})
    as_person("vw-1")  # every role may Stop
    stopped = stop(api)
    as_person("ag-1")
    resumed = resume(api, worker)
    as_person("ow-1")
    acked = ack(api, seeded)

    as_person("vw-1")  # every role may read it
    items = operations(api)["items"]

    assert [(item["kind"], item["cmdId"] or item["alert"]["id"]) for item in items] == [
        ("ACK", acked), ("RESUME", resumed), ("STOP", stopped), ("DOSE", dosed),
    ]
    assert [item["by"] for item in items] == [
        {"id": "ow-1", "name": "Farm Owner", "role": "OWNER"},
        {"id": "ag-1", "name": "Asha Agronomist", "role": "AGRONOMIST"},
        {"id": "vw-1", "name": "Vani Viewer", "role": "VIEWER"},
        {"id": "op-1", "name": "Ravi Operator", "role": "OPERATOR"},
    ]
    times = [datetime.fromisoformat(item["at"]) for item in items]
    assert times == sorted(times, reverse=True)
    assert all(datetime.now(UTC) - moment < timedelta(minutes=1) for moment in times)


def test_a_dose_says_what_it_delivered_and_ec_and_ph_before_and_after(api, worker):
    as_person("op-1")
    cmd_id = dose(api, worker, before={"ec": 1.51, "ph": 6.21}, after={"ec": 1.63, "ph": 6.02})
    [item] = operations(api)["items"]
    assert item["kind"] == "DOSE"
    assert item["zone"] == "Z1"
    assert item["target"] == {"zone": "Z1"}
    assert item["cmdId"] == cmd_id
    assert item["result"] == {"state": "COMPLETED", "reason": None}
    assert item["lines"] == [
        {"line": line["line"], "requestedMl": line["ml"], "deliveredMl": line["ml"], "state": "DONE"}
        for line in DOSE["lines"]
    ]
    assert item["before"] == {"ec": 1.51, "ph": 6.21}
    assert item["after"] == {"ec": 1.63, "ph": 6.02}
    assert item["alert"] is None


def test_a_stop_and_a_resume_say_their_target_and_result(api, worker):
    as_person("vw-1")
    stop(api, {"scope": "ZONE", "zone": "Z1"})
    as_person("ag-1")
    resume(api, worker)
    resumed, stopped = operations(api)["items"]
    assert stopped["target"] == {"scope": "ZONE", "zone": "Z1"}
    assert stopped["zone"] == "Z1"
    assert stopped["result"] == {"state": "DISPATCHED", "reason": None}  # nothing heard back yet (§8.1)
    assert resumed["target"] == {"zone": "Z1"}
    assert [stopped["lines"], stopped["before"], stopped["after"]] == [None, None, None]  # a dose's only


def test_an_ack_names_its_alert(api, seeded):
    as_person("ow-1")
    alert_id = ack(api, seeded)
    [item] = operations(api)["items"]
    assert item["kind"] == "ACK"
    assert item["zone"] == "Z1"
    assert item["result"] == {"state": "ACKNOWLEDGED", "reason": None}
    assert item["alert"] == {
        "id": alert_id, "kind": "EC_HIGH", "severity": "CRITICAL",
        "message": "EC in Zone A is 2.6 mS/cm, above its alarm limit",
    }
    assert [item["cmdId"], item["target"], item["lines"]] == [None, None, None]


def test_the_result_follows_the_controller_and_a_command_left_without_replies_is_unknown(api, worker, seeded):
    as_person("op-1")
    cmd_id = dose(api, worker)
    with seeded.begin() as connection:
        connection.execute(update(SentCommand).values(timed_out_at=datetime.now(UTC)))
    assert operations(api)["items"][0]["result"] == {"state": "COMPLETED", "reason": None}  # final: stays
    stopped = stop(api)
    with seeded.begin() as connection:
        marked = update(SentCommand).where(SentCommand.cmd_id == stopped)
        connection.execute(marked.values(timed_out_at=datetime.now(UTC)))
    assert operations(api, kind="STOP")["items"][0]["result"] == {"state": "UNKNOWN", "reason": "TIMED_OUT"}
    assert cmd_id != stopped


def test_the_role_is_the_one_they_had_when_they_did_it(api, worker, seeded):
    as_person("ag-1")
    resume(api, worker)
    with seeded.begin() as connection:  # promoted afterwards
        connection.execute(update(User).where(User.user_id == "ag-1").values(role="OWNER"))
    assert operations(api)["items"][0]["by"]["role"] == "AGRONOMIST"  # from the audit log


def test_a_stop_shows_the_role_they_have_now(api, seeded):
    """A stop's session is checked without the database (§8.3 rule 9), so its role isn't recorded."""
    as_person("ag-1")
    stop(api)
    with seeded.begin() as connection:
        connection.execute(update(User).where(User.user_id == "ag-1").values(role="OWNER"))
    assert operations(api)["items"][0]["by"] == {"id": "ag-1", "name": "Asha Agronomist", "role": "OWNER"}


def test_someone_the_database_doesnt_know_still_shows_by_their_id(api, worker):
    sign_in_as(app, "OPERATOR", user_id="gone-1")
    resume(api, worker)
    stop(api)
    stopped, resumed = operations(api)["items"]
    assert resumed["by"] == {"id": "gone-1", "name": None, "role": "OPERATOR"}
    assert stopped["by"] == {"id": "gone-1", "name": None, "role": None}


def test_a_dose_the_broker_never_took_isnt_listed(api, worker, publisher):
    as_person("op-1")
    request_id = reviewed(api, worker)
    publisher.broker_down = True
    assert confirm(api, request_id, uuid.uuid4()).status_code == 503
    assert operations(api)["items"] == []


# --- Filters and pages -------------------------------------------------------------------------


def test_kind_keeps_one_kind(api, worker, seeded):
    as_person("op-1")
    dose(api, worker)
    stop(api)
    ack(api, seeded)
    assert [item["kind"] for item in operations(api, kind="STOP")["items"]] == ["STOP"]
    assert [item["kind"] for item in operations(api, kind="ACK")["items"]] == ["ACK"]
    assert [item["kind"] for item in operations(api, kind="DOSE")["items"]] == ["DOSE"]
    assert operations(api, kind="RESUME")["items"] == []


def test_zone_keeps_that_zones_actions_and_the_stops_of_every_zone(api, worker, seeded):
    as_person("ag-1")
    stop(api, {"scope": "ZONE", "zone": "Z2"})
    stop(api)  # ALL_OPERATIONS: Z1 too
    resume(api, worker)  # Z1
    ack(api, seeded, zone="Z2")
    assert [(item["kind"], item["target"]) for item in operations(api, zone="Z1")["items"]] == [
        ("RESUME", {"zone": "Z1"}), ("STOP", {"scope": "ALL_OPERATIONS"}),
    ]
    assert [item["kind"] for item in operations(api, zone="Z2")["items"]] == ["ACK", "STOP", "STOP"]


def test_a_page_at_a_time_with_next_until_the_last(api, seeded):
    as_person("ow-1")
    made = [stop(api) for _ in range(3)] + [ack(api, seeded) for _ in range(2)]
    seen, page = [], operations(api, limit=2)
    while True:
        seen += [item["cmdId"] or item["alert"]["id"] for item in page["items"]]
        if page["next"] is None:
            break
        page = operations(api, limit=2, before=page["next"])
    assert seen == list(reversed(made))  # each once, newest first


def test_actions_at_the_same_moment_each_show_once_across_pages(api, seeded):
    as_person("ow-1")
    made = {stop(api) for _ in range(3)} | {ack(api, seeded) for _ in range(2)}
    moment = datetime(2026, 11, 20, 4, 55, tzinfo=UTC)
    with seeded.begin() as connection:
        connection.execute(update(SentCommand).values(issued_at=moment))
        connection.execute(update(Alert).values(acked_at=moment))
    seen, page = [], operations(api, limit=1)
    while True:
        seen += [item["cmdId"] or item["alert"]["id"] for item in page["items"]]
        if page["next"] is None:
            break
        page = operations(api, limit=1, before=page["next"])
    assert sorted(seen) == sorted(made)
    assert seen == sorted(seen, reverse=True)  # by ID within the same moment


def test_before_may_also_be_a_plain_time(api):
    as_person("ow-1")
    stop(api)
    assert operations(api, before="2026-01-01T00:00:00Z")["items"] == []
    assert len(operations(api, before="2999-01-01T00:00:00Z")["items"]) == 1


def test_one_page_that_holds_everything_has_no_next(api):
    as_person("ow-1")
    stop(api)
    assert operations(api, limit=1)["next"] is None


@pytest.mark.parametrize("query", [{"kind": "REFILL"}, {"limit": 0}, {"limit": 501}, {"before": "yesterday"}])
def test_a_query_that_isnt_one_is_422(api, query):
    as_person("ow-1")
    assert_error(api.get("/api/operations", params=query), 422, "VALIDATION_ERROR")


def test_another_sites_actions_never_show(seeded, pack, publisher, tmp_path):
    other_pack = write_dosing_pack(tmp_path / "second" / ORG, LAYOUT | {"site": "another-site"})
    seed(seeded, other_pack)
    as_person("op-1")
    other = client_for(other_pack, seeded, publisher)
    assert other.post("/api/stop", json={"scope": "ALL_OPERATIONS"}).status_code == 202
    assert len(operations(other)["items"]) == 1
    assert operations(client_for(pack, seeded, publisher))["items"] == []  # this site's API again


def test_a_database_that_cant_answer_is_503(pack, no_database, publisher):
    as_person("ow-1")
    assert_error(client_for(pack, no_database, publisher).get("/api/operations"), 503, "SERVICE_UNAVAILABLE")
