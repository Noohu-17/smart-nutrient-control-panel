"""Alerts (T1-24, contract §11.2): raised and cleared by the worker, listed and acknowledged by
the API.

Messages go in through the worker, as in test_dose_requests, with the limits and delays from
client_A's alerts.json, so the numbers come from the config as in production.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, select, update
from test_dose_requests import ORG, SITE, handle, telemetry, write_dosing_pack
from test_event import event
from test_read_api import api_client

from app.alerts import AlertRules
from app.api.pack import SiteRef
from app.api.sim.routes import record_reset
from app.db.models import Alert, AuditLog, User
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker
from conftest import sign_in_as

TELEMETRY = "farm/siet-ph1/zone/Z1/telemetry"
EVENTS = "farm/siet-ph1/zone/Z1/event"


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
def worker(seeded, pack):
    return Worker(seeded, ORG, SITE, rules=AlertRules.from_pack(pack))


@pytest.fixture
def api(seeded, pack):
    return api_client(pack, seeded)


class Readings:
    """Zone A telemetry, each newer than the last, so the worker keeps it as the latest."""

    def __init__(self, worker):
        self.worker, self.seq = worker, 100

    def send(self, readings=None, quality=None):
        self.seq += 1
        handle(self.worker, telemetry(2, readings, quality, seq=self.seq), TELEMETRY)


@pytest.fixture
def readings(worker):
    return Readings(worker)


def alerts(api, **query):
    response = api.get("/api/alerts", params=query)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def only(api, **query):
    [found] = alerts(api, **query)
    return found


# --- Rules on readings ----------------------------------------------------------------------


def test_ec_above_the_alarm_limit_raises_an_alert_in_plain_words(api, readings):
    readings.send({"ec": 2.61})
    found = only(api)
    assert {k: found[k] for k in ("kind", "severity", "status", "zone")} == {
        "kind": "EC_HIGH", "severity": "CRITICAL", "status": "OPEN", "zone": "Z1",
    }
    assert found["message"] == "EC in Zone 1 is 2.61 mS/cm, above the 2.4 alarm limit"
    assert found["systemAction"] and found["nextStep"]
    assert found["raisedAt"] and found["clearedAt"] is None


def test_the_alert_clears_when_a_reading_is_back_within_the_limit(api, readings):
    readings.send({"ec": 2.61})
    readings.send({"ec": 1.9})
    found = only(api)
    assert found["status"] == "CLEARED" and found["clearedAt"]
    assert alerts(api, status="OPEN") == []


def test_a_condition_that_lasts_gives_one_alert(api, readings):
    for ec in (2.5, 2.6, 2.7):
        readings.send({"ec": ec})
    assert len(alerts(api)) == 1


def test_a_lasting_alert_says_the_latest_value(api, readings):
    readings.send({"ec": 2.51})
    readings.send({"ec": 2.8})
    assert only(api)["message"] == "EC in Zone 1 is 2.8 mS/cm, above the 2.4 alarm limit"


def test_an_alert_raised_as_a_warning_before_turns_critical_on_the_next_reading(api, readings, seeded):
    readings.send({"ec": 2.51})
    with seeded.begin() as connection:  # as an EC alert opened before EC_HIGH was CRITICAL
        connection.execute(update(Alert).values(severity="WARNING"))
    assert only(api)["severity"] == "WARNING"
    readings.send({"ec": 2.6})
    assert only(api)["severity"] == "CRITICAL"


def test_warm_solution_raises_its_own_alert(api, readings):
    readings.send({"solTempC": 27.5})
    found = only(api)
    assert found["kind"] == "SOL_TEMP_HIGH"
    assert found["message"] == "Solution in Zone 1 is 27.5 °C, above the 26 °C alarm limit"


def test_a_reading_that_isnt_ok_neither_raises_nor_clears(api, readings):
    readings.send({"ec": 3.0}, {"ec": "UNCALIBRATED"})
    assert alerts(api) == []
    readings.send({"ec": 2.61})
    readings.send({"ec": None}, {"ec": "FAULT"})  # a faulty probe says nothing about the water
    assert only(api)["status"] == "OPEN"


def test_an_older_reading_cant_undo_a_newer_one(api, readings, worker):
    readings.send({"ec": 2.61})
    handle(worker, telemetry(5, {"ec": 1.6}, seq=50), TELEMETRY)  # seq below the kept one
    assert only(api)["status"] == "OPEN"


def test_a_low_reservoir_is_raised_at_once(api, readings):
    readings.send({"reservoirPct": 15})  # alerts.json RESERVOIR_LOW delayS 0
    found = only(api)
    assert (found["kind"], found["message"]) == ("RESERVOIR_LOW", "Zone 1 reservoir is at 15 %, below 40 %")


def test_no_flow_waits_out_its_delay_before_it_is_raised(api, readings, seeded):
    readings.send({"circFlowLpm": 3.0})
    assert alerts(api) == []  # alerts.json CIRC_NO_FLOW delayS 30: not yet
    with seeded.begin() as connection:  # as if the first reading without flow came 31 s ago
        connection.execute(update(Alert).values(first_seen_at=datetime.now(UTC) - timedelta(seconds=31)))
    readings.send({"circFlowLpm": 3.0})
    found = only(api)
    assert (found["kind"], found["severity"]) == ("CIRC_NO_FLOW", "CRITICAL")
    assert found["message"] == "Circulation in Zone 1 is 3 L/min, below 15 L/min"


def test_a_condition_that_ends_within_its_delay_leaves_nothing(api, readings, seeded):
    readings.send({"circFlowLpm": 3.0})
    readings.send({"circFlowLpm": 31.0})
    with seeded.connect() as connection:
        assert connection.scalar(select(Alert.alert_id)) is None


# --- Events ---------------------------------------------------------------------------------


def test_an_estop_event_raises_a_critical_alert_with_its_own_words(api, worker):
    handle(worker, event(msgId=str(uuid.uuid4())), EVENTS)
    found = only(api)
    assert (found["kind"], found["severity"], found["zone"]) == ("ESTOP_PRESSED", "CRITICAL", "Z1")
    assert found["message"] == "E-STOP pressed on the Zone 1 dosing cabinet"
    assert found["nextStep"] == "Check the cabinet, release the E-STOP, then press Resume in the app"


def test_its_release_clears_it(api, worker):
    handle(worker, event(msgId=str(uuid.uuid4())), EVENTS)
    handle(worker, event(
        msgId=str(uuid.uuid4()), seq=11, kind="ESTOP_RELEASED", severity="INFO",
        message="E-STOP released", systemAction="Zone 1 stays stopped", nextStep="Press Resume",
    ), EVENTS)
    assert only(api)["status"] == "CLEARED"


def test_an_info_event_on_its_own_raises_nothing(api, worker):
    handle(worker, event(
        msgId=str(uuid.uuid4()), kind="ESTOP_RELEASED", severity="INFO",
        message="E-STOP released", systemAction="Zone 1 stays stopped", nextStep="Press Resume",
    ), EVENTS)
    assert alerts(api) == []


# --- Acknowledge, filters and reset -------------------------------------------------------------


def test_acknowledging_records_who_and_keeps_the_alert_until_its_condition_ends(api, readings, seeded):
    with seeded.begin() as connection:
        connection.execute(insert(User).values(
            org_id=ORG, site_id=SITE, user_id="op-1", name="Olive Operator", email="olive@example.com",
            role="OPERATOR",
        ))
    sign_in_as(app, "OPERATOR", user_id="op-1")
    readings.send({"ec": 2.61})
    alert_id = only(api)["id"]
    response = api.post(f"/api/alerts/{alert_id}/ack")
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["acknowledgedBy"]) == ("ACKNOWLEDGED", "Olive Operator")
    assert alerts(api, status="OPEN") == []
    assert len(alerts(api, status="ACKNOWLEDGED")) == 1
    readings.send({"ec": 1.9})
    assert only(api, status="CLEARED")["acknowledgedBy"] == "Olive Operator"
    with seeded.connect() as connection:
        assert connection.scalar(select(Alert.acked_by)) == "op-1"
        logged = connection.execute(select(AuditLog.user_id, AuditLog.role, AuditLog.action, AuditLog.target)).all()
    assert logged == [("op-1", "OPERATOR", "ALERT_ACK", alert_id)]


def test_a_viewer_cant_acknowledge_and_an_unknown_alert_is_a_404(api, readings):
    readings.send({"ec": 2.61})
    alert_id = only(api)["id"]
    sign_in_as(app, "VIEWER")
    refused = api.post(f"/api/alerts/{alert_id}/ack")
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "FORBIDDEN")
    assert only(api)["status"] == "OPEN"
    sign_in_as(app, "OWNER")
    missing = api.post("/api/alerts/nope/ack")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")


def test_filters_by_severity_and_zone(api, readings, worker):
    readings.send({"solTempC": 27.5})
    handle(worker, event(msgId=str(uuid.uuid4())), EVENTS)
    assert [a["kind"] for a in alerts(api, severity="CRITICAL")] == ["ESTOP_PRESSED"]
    assert len(alerts(api, zone="Z1")) == 2 and alerts(api, zone="Z2") == []


def test_a_playground_reset_clears_every_alert_and_hides_older_ones(api, readings, seeded):
    readings.send({"ec": 2.61})
    record_reset(seeded, SiteRef(ORG, SITE), datetime.now(UTC))
    assert alerts(api) == []
    with seeded.connect() as connection:
        assert connection.scalar(select(Alert.cleared_at)) is not None
    readings.send({"ec": 2.7})  # the condition is still there after the reset: a new alert
    assert only(api)["status"] == "OPEN"


def test_without_rules_the_worker_raises_nothing(api, seeded):
    plain = Worker(seeded, ORG, SITE)
    handle(plain, telemetry(2, {"ec": 2.61}, seq=300), TELEMETRY)
    assert alerts(api) == []
