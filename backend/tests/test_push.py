"""Web Push for alerts (T1-41, contract §11.2): subscriptions through the API, the worker's outbox,
and the notifier's sends, with a fake push service: no test reaches the internet.

The API tests go through the real sign-in (test_auth.py's helpers), as push is about real users.
"""

import base64
import dataclasses
import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from test_auth import HASH, OWNER_TOTP, client_on, sign_in
from test_event import event
from test_read_api import ORG, write_pack

from app import alerts
from app.api.routes import get_settings
from app.config import load_settings
from app.contract import parse_farm_message
from app.db.models import Alert, PushOutbox, PushSubscription, User
from app.db.seed import seed
from app.main import app
from app.push import endpoints, keys, notifier
from app.push.notifier import Answer, push_due
from app.push.rules import PushRules

SITE = "siet-ph1"
ROLES = ("OWNER", "AGRONOMIST", "OPERATOR", "VIEWER")
P256DH = base64.urlsafe_b64encode(b"\x04" + bytes(range(64))).rstrip(b"=").decode()
AUTH = base64.urlsafe_b64encode(bytes(16)).rstrip(b"=").decode()
PUBLIC = ["142.250.1.1"]  # what a push service's name resolves to, in these tests
ALERTS = {
    "v": 1,
    "delivery": ["IN_APP", "WEB_PUSH"],
    "events": {
        "LEAK_DETECTED": {"delayS": 0, "notifyRoles": ["OWNER", "AGRONOMIST", "OPERATOR"]},
        "PUMP_FAULT": {"delayS": 0, "notifyRoles": ["OWNER"]},
    },
}


def endpoint(name: str) -> str:
    return f"https://fcm.googleapis.com/fcm/send/{name}"


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pack(tmp_path):
    folder = write_pack(tmp_path / ORG)
    (folder / "alerts.json").write_text(json.dumps(ALERTS), encoding="utf-8")
    return folder


@pytest.fixture
def seeded(migrated_db, pack):
    """The site, and one user per role with test_auth's password: owner@example.com …"""
    seed(migrated_db, pack)
    with migrated_db.begin() as connection:
        for role in ROLES:
            name = role.lower()
            two_factor = {"totp_secret": OWNER_TOTP, "totp_enabled_at": NOW} if role == "OWNER" else {}
            connection.execute(insert(User).values(
                org_id=ORG, site_id=SITE, user_id=name, name=f"{role.title()} Person",
                email=f"{name}@example.com", role=role, password_hash=HASH, **two_factor,
            ))
    return migrated_db


def push_client(pack, engine, vapid_public_key="BPublicKeyForTests") -> TestClient:
    client = client_on(pack, engine)
    settings = dataclasses.replace(load_settings(), client_dir=pack, vapid_public_key=vapid_public_key)
    app.dependency_overrides[get_settings] = lambda: settings
    return client


@pytest.fixture
def api(seeded, pack):
    return push_client(pack, seeded)


def subscribe(api, csrf, name="phone", **changes):
    body = {"endpoint": endpoint(name), "keys": {"p256dh": P256DH, "auth": AUTH}} | changes
    return api.post("/api/push/subscriptions", json=body, headers=csrf)


def rows(engine, model):
    with engine.connect() as connection:
        return connection.execute(select(model.__table__)).all()


def error(response):
    return response.status_code, response.json()["error"]["code"]


# --- Subscriptions through the API ---------------------------------------------------------------


def test_a_browser_subscribes_for_critical_alerts_unless_it_asks_for_more(api, seeded):
    csrf = sign_in(api, "OPERATOR")
    response = subscribe(api, csrf)
    assert response.status_code == 200
    assert response.json() == {"endpoint": endpoint("phone"), "severities": ["CRITICAL"]}
    response = subscribe(api, csrf, severities=["WARNING", "CRITICAL"])
    assert response.json()["severities"] == ["CRITICAL", "WARNING"]
    [row] = rows(seeded, PushSubscription)
    assert (row.user_id, row.severities, row.p256dh, row.auth) == ("operator", ["CRITICAL", "WARNING"], P256DH, AUTH)


def test_critical_is_always_in_a_browsers_severities(api, seeded):
    """Critical always notifies, as in the app (#91): asking for warnings only still gets critical."""
    csrf = sign_in(api, "OPERATOR")
    assert subscribe(api, csrf, severities=["WARNING"]).json()["severities"] == ["CRITICAL", "WARNING"]
    [row] = rows(seeded, PushSubscription)
    assert row.severities == ["CRITICAL", "WARNING"]


def test_a_browser_that_subscribes_as_someone_else_is_theirs_now(api, seeded, pack):
    subscribe(api, sign_in(api, "OPERATOR"))
    other = push_client(pack, seeded)
    subscribe(other, sign_in(other, "AGRONOMIST"))
    assert [row.user_id for row in rows(seeded, PushSubscription)] == ["agronomist"]


def test_unsubscribing_removes_only_your_own_browser(api, seeded, pack):
    subscribe(api, sign_in(api, "OPERATOR"))
    other = push_client(pack, seeded)
    other_csrf = sign_in(other, "AGRONOMIST")
    response = other.request("DELETE", "/api/push/subscriptions", json={"endpoint": endpoint("phone")}, headers=other_csrf)
    assert response.status_code == 204
    assert len(rows(seeded, PushSubscription)) == 1
    csrf = sign_in(api, "OPERATOR")
    assert api.request("DELETE", "/api/push/subscriptions", json={"endpoint": endpoint("phone")},
                       headers=csrf).status_code == 204
    assert rows(seeded, PushSubscription) == []


@pytest.mark.parametrize("url", [
    "http://db:5432/", "https://10.0.0.5/push", "https://[::1]/push", "https://example.org/push",
    "https://fcm.googleapis.com.example.org/push", "https://fcm.googleapis.com:8443/push",
    "https://user:pw@fcm.googleapis.com/push", "http://fcm.googleapis.com/push", "fcm.googleapis.com/push",
    "https://fcm.googleapis.com/" + "x" * 2100,
])
def test_an_endpoint_that_isnt_a_push_services_is_refused(api, seeded, url):
    assert error(subscribe(api, sign_in(api, "OPERATOR"), endpoint=url)) == (422, "VALIDATION_ERROR")
    assert rows(seeded, PushSubscription) == []


@pytest.mark.parametrize("keys", [
    {"p256dh": "not base64!", "auth": AUTH}, {"p256dh": AUTH, "auth": AUTH}, {"p256dh": P256DH, "auth": P256DH},
])
def test_keys_of_the_wrong_size_are_refused(api, keys):
    assert error(subscribe(api, sign_in(api, "OPERATOR"), keys=keys)) == (422, "VALIDATION_ERROR")


def test_severities_are_critical_or_warning_and_at_least_one(api):
    csrf = sign_in(api, "OPERATOR")
    assert error(subscribe(api, csrf, severities=[])) == (422, "VALIDATION_ERROR")
    assert error(subscribe(api, csrf, severities=["INFO"])) == (422, "VALIDATION_ERROR")


def test_a_demo_user_cant_subscribe(api, seeded):
    csrf = sign_in(api, "OPERATOR")
    with seeded.begin() as connection:  # signed in without a password: what POST /api/auth/demo gives
        connection.execute(update(User).where(User.user_id == "operator").values(password_hash=None))
    assert error(subscribe(api, csrf)) == (403, "FORBIDDEN")


def test_without_web_push_in_delivery_the_routes_are_404(seeded, pack):
    (pack / "alerts.json").write_text(json.dumps(ALERTS | {"delivery": ["IN_APP"]}), encoding="utf-8")
    api = push_client(pack, seeded)
    csrf = sign_in(api, "OPERATOR")
    assert error(api.get("/api/push/key")) == (404, "NOT_FOUND")
    assert error(subscribe(api, csrf)) == (404, "NOT_FOUND")


def test_the_key_is_the_public_one_and_503_until_it_is_set(seeded, pack):
    api = push_client(pack, seeded)
    sign_in(api, "VIEWER")
    assert api.get("/api/push/key").json() == {"publicKey": "BPublicKeyForTests"}
    api = push_client(pack, seeded, vapid_public_key=None)
    sign_in(api, "VIEWER")
    assert error(api.get("/api/push/key")) == (503, "SERVICE_UNAVAILABLE")


def test_signing_out_everywhere_stops_that_users_pushes(api, seeded):
    csrf = sign_in(api, "OPERATOR")
    subscribe(api, csrf)
    assert api.post("/api/auth/logout-all", headers=csrf).status_code == 204
    assert rows(seeded, PushSubscription) == []


def test_turning_a_user_off_stops_their_pushes(api, seeded, pack):
    subscribe(api, sign_in(api, "OPERATOR"))
    owner = push_client(pack, seeded)
    response = owner.patch("/api/users/operator", json={"active": False}, headers=sign_in(owner, "OWNER"))
    assert response.status_code == 200
    assert rows(seeded, PushSubscription) == []


# --- The worker's outbox -----------------------------------------------------------------------------

NOW = datetime(2026, 11, 20, 6, 0, tzinfo=UTC)


def raise_event(engine, at=NOW, **changes):
    with engine.begin() as connection:
        alerts.on_event(connection, ORG, parse_farm_message(event(**changes)), at)


def ec_high(severity: str) -> alerts.Condition:
    return alerts.Condition("EC_HIGH", "EC_HIGH:Z1", True, severity, "EC in Zone A is 2.6", "No action", "Dilute")


def judge(engine, condition, delay=timedelta(0), at=NOW):
    with engine.begin() as connection:
        alerts.judge(connection, ORG, SITE, "Z1", condition, delay, at, None)


def outbox(engine):
    return [(row.severity, row.outcome) for row in rows(engine, PushOutbox)]


def test_raising_an_alert_queues_one_push_however_often_its_event_comes(seeded):
    raise_event(seeded)
    raise_event(seeded, at=NOW + timedelta(seconds=5))
    assert outbox(seeded) == [("CRITICAL", None)]


def test_an_alert_that_turns_critical_is_queued_again_but_not_back_to_warning(seeded):
    judge(seeded, ec_high("WARNING"))
    judge(seeded, ec_high("WARNING"))
    judge(seeded, ec_high("CRITICAL"))
    judge(seeded, ec_high("CRITICAL"))
    judge(seeded, ec_high("WARNING"))
    assert sorted(outbox(seeded)) == [("CRITICAL", None), ("WARNING", None)]


def test_an_alert_waiting_out_its_delay_is_queued_when_it_is_raised(seeded):
    judge(seeded, ec_high("WARNING"), delay=timedelta(seconds=60))
    assert outbox(seeded) == []
    judge(seeded, ec_high("WARNING"), delay=timedelta(seconds=60), at=NOW + timedelta(seconds=61))
    assert outbox(seeded) == [("WARNING", None)]


# --- The notifier ------------------------------------------------------------------------------------


class PushService:
    """A fake push service: records each push, and answers from `answers` (then 201), or for an
    endpoint from `by_endpoint`. The notifier pushes from several threads at once."""

    def __init__(self, *answers: Answer, by_endpoint=None):
        self.answers = list(answers)
        self.by_endpoint = dict(by_endpoint or {})
        self.pushes = []
        self.lock = threading.Lock()

    def __call__(self, subscription, data, headers):
        with self.lock:
            self.pushes.append((subscription["endpoint"], json.loads(data), dict(headers)))
            if subscription["endpoint"] in self.by_endpoint:
                return self.by_endpoint[subscription["endpoint"]]
            return self.answers.pop(0) if self.answers else Answer(201)

    @property
    def endpoints(self):
        return [pushed[0] for pushed in self.pushes]


def add_browser(engine, user_id, name=None, severities=("CRITICAL",), url=None):
    with engine.begin() as connection:
        connection.execute(insert(PushSubscription).values(
            org_id=ORG, site_id=SITE, subscription_id=f"sub-{name or user_id}", user_id=user_id,
            endpoint=url or endpoint(name or user_id), p256dh=P256DH, auth=AUTH, severities=list(severities),
            created_at=NOW, updated_at=NOW,
        ))


def push(engine, service, rules=None, sleeps=None, at=NOW + timedelta(seconds=2), resolve=lambda host: PUBLIC,
         beat=lambda: None, sleep=None):
    rules = rules or PushRules(enabled=True, roles={k: tuple(v["notifyRoles"]) for k, v in ALERTS["events"].items()})
    sleep = sleep or (sleeps.append if sleeps is not None else lambda s: None)
    return push_due(engine, ORG, SITE, rules, service, sleep=sleep,
                    resolve=resolve, clock=lambda: at, beat=beat)


@pytest.fixture
def browsers(seeded):
    """One browser per role, critical alerts only."""
    for role in ROLES:
        add_browser(seeded, role.lower())
    return seeded


def test_a_critical_alert_goes_to_each_browser_whose_user_the_kind_names(browsers):
    raise_event(browsers, kind="LEAK_DETECTED", message="Leak in Zone A", nextStep="Close the valve")
    service = PushService()
    assert push(browsers, service) == 1
    assert sorted(service.endpoints) == [endpoint("agronomist"), endpoint("operator"), endpoint("owner")]
    _, data, headers = service.pushes[0]
    assert data["title"] == "Critical: Leak in Zone A" and data["body"] == "Close the valve"
    assert data["severity"] == "CRITICAL" and data["kind"] == "LEAK_DETECTED" and data["zone"] == "Z1"
    with browsers.connect() as connection:
        assert data["alertId"] == connection.scalar(select(Alert.alert_id))
    assert headers["Urgency"] == "high" and len(headers["Topic"]) == 32
    assert outbox(browsers) == [("CRITICAL", "SENT")]
    assert push(browsers, PushService()) == 0  # once per alert


def test_a_kind_alerts_json_doesnt_list_goes_to_owner_agronomist_and_operator(browsers):
    raise_event(browsers)  # ESTOP_PRESSED: not in the test's alerts.json
    service = PushService()
    push(browsers, service)
    assert sorted(service.endpoints) == [endpoint("agronomist"), endpoint("operator"), endpoint("owner")]


def test_a_kind_for_owners_only_reaches_only_owners(browsers):
    raise_event(browsers, kind="PUMP_FAULT")
    service = PushService()
    push(browsers, service)
    assert service.endpoints == [endpoint("owner")]


def test_a_warning_goes_only_to_browsers_that_asked_for_warnings(browsers):
    add_browser(browsers, "operator", name="operator-laptop", severities=("CRITICAL", "WARNING"))
    judge(browsers, ec_high("WARNING"))
    service = PushService()
    push(browsers, service)
    assert service.endpoints == [endpoint("operator-laptop")]
    assert service.pushes[0][1]["title"].startswith("Warning: ") and service.pushes[0][2]["Urgency"] == "normal"


def test_nothing_is_sent_for_an_alert_acknowledged_or_cleared_before_its_push(browsers):
    raise_event(browsers)
    raise_event(browsers, zone="Z2", device="ZC2")
    with browsers.begin() as connection:
        first, second = connection.scalars(select(Alert.alert_id).order_by(Alert.zone_id)).all()
        connection.execute(update(Alert).where(Alert.alert_id == first).values(acked_at=NOW, acked_by="owner"))
        connection.execute(update(Alert).where(Alert.alert_id == second).values(cleared_at=NOW))
    service = PushService()
    assert push(browsers, service) == 2
    assert service.pushes == []
    assert sorted(outbox(browsers)) == [("CRITICAL", "ACKNOWLEDGED"), ("CRITICAL", "CLEARED")]


def test_a_push_queued_too_long_ago_is_skipped(browsers):
    raise_event(browsers)
    service = PushService()
    push(browsers, service, at=NOW + timedelta(minutes=11))
    assert service.pushes == [] and outbox(browsers) == [("CRITICAL", "TOO_OLD")]


def test_a_warning_that_turned_critical_before_its_push_goes_once_as_critical(browsers):
    add_browser(browsers, "operator", name="operator-laptop", severities=("CRITICAL", "WARNING"))
    judge(browsers, ec_high("WARNING"))
    judge(browsers, ec_high("CRITICAL"))
    service = PushService()
    push(browsers, service)
    assert {pushed[1]["severity"] for pushed in service.pushes} == {"CRITICAL"}
    assert sorted(outbox(browsers)) == [("CRITICAL", "SENT"), ("WARNING", "SEVERITY_CHANGED")]


def test_a_turned_off_user_gets_nothing(browsers):
    with browsers.begin() as connection:
        connection.execute(update(User).where(User.user_id == "operator").values(disabled_at=NOW))
    raise_event(browsers)
    service = PushService()
    push(browsers, service)
    assert endpoint("operator") not in service.endpoints


def test_with_push_off_queued_alerts_are_skipped(browsers):
    raise_event(browsers)
    service = PushService()
    push(browsers, service, rules=PushRules(enabled=False))
    assert service.pushes == [] and outbox(browsers) == [("CRITICAL", "PUSH_OFF")]


def test_a_browser_that_has_gone_is_removed_and_the_others_still_get_it(browsers):
    raise_event(browsers, kind="PUMP_FAULT")
    add_browser(browsers, "owner", name="owner-laptop")
    service = PushService(by_endpoint={endpoint("owner"): Answer(410)})
    push(browsers, service)
    assert len(service.pushes) == 2
    assert [row.subscription_id for row in rows(browsers, PushSubscription) if row.user_id == "owner"] == ["sub-owner-laptop"]
    assert outbox(browsers) == [("CRITICAL", "SENT")]


def test_too_many_requests_waits_its_retry_after_then_sends(browsers):
    raise_event(browsers, kind="PUMP_FAULT")
    sleeps = []
    service = PushService(Answer(429, retry_after=3), Answer(503))
    push(browsers, service, sleeps=sleeps)
    assert len(service.pushes) == 3 and sleeps == [3, 2.0]
    assert outbox(browsers) == [("CRITICAL", "SENT")]


def test_a_push_service_that_keeps_failing_is_given_up_after_three_tries(browsers):
    raise_event(browsers, kind="PUMP_FAULT")
    sleeps = []
    service = PushService(Answer(500), Answer(0), Answer(502, retry_after=60))
    push(browsers, service, sleeps=sleeps)
    assert len(service.pushes) == 3 and sleeps == [1.0, 2.0]  # Retry-After is capped at MAX_WAIT_S, never longer
    assert outbox(browsers) == [("CRITICAL", "NO_ONE")]
    assert len(rows(browsers, PushSubscription)) == 4  # kept: it may answer next time


def test_a_refusal_other_than_gone_or_busy_isnt_tried_again(browsers):
    raise_event(browsers, kind="PUMP_FAULT")
    service = PushService(Answer(403))
    push(browsers, service)
    assert len(service.pushes) == 1 and outbox(browsers) == [("CRITICAL", "NO_ONE")]


def test_an_endpoint_that_isnt_a_push_services_is_never_sent_to_and_removed(seeded):
    add_browser(seeded, "owner", url="http://db:5432/")  # saved before the check existed, or written by hand
    raise_event(seeded, kind="PUMP_FAULT")
    service = PushService()
    push(seeded, service)
    assert service.pushes == [] and rows(seeded, PushSubscription) == []


@pytest.mark.parametrize("resolved", [
    ["10.0.0.7"],  # a push service's name, pointed inside the network
    ["127.0.0.1", "142.250.1.1"],
    [],  # no address
])
def test_an_endpoint_whose_address_isnt_public_is_never_sent_to_but_kept(seeded, resolved):
    add_browser(seeded, "owner")
    raise_event(seeded, kind="PUMP_FAULT")
    service = PushService()
    sleeps = []
    push(seeded, service, sleeps=sleeps, resolve=lambda host: resolved)
    assert service.pushes == [] and len(rows(seeded, PushSubscription)) == 1
    assert sleeps == [1.0, 2.0] and outbox(seeded) == [("CRITICAL", "NO_ONE")]  # tried like no answer


def test_a_failed_lookup_keeps_every_subscription_and_the_next_try_sends(browsers, caplog):
    """DNS down for a moment must not remove everyone's notifications."""
    raise_event(browsers, kind="LEAK_DETECTED")
    dns_down = threading.Event()
    dns_down.set()

    def resolve(host):
        if dns_down.is_set():
            raise OSError("Temporary failure in name resolution")
        return PUBLIC

    caplog.set_level(logging.INFO)
    service = PushService()
    push(browsers, service, resolve=resolve, sleep=lambda seconds: dns_down.clear())  # back after the first wait
    assert len(rows(browsers, PushSubscription)) == 4
    assert len(service.pushes) == 3 and outbox(browsers) == [("CRITICAL", "SENT")]
    assert "lookup failed" in caplog.text


def test_a_lookup_that_keeps_failing_gives_up_and_keeps_the_subscription(seeded):
    add_browser(seeded, "owner")
    raise_event(seeded, kind="PUMP_FAULT")

    def resolve(host):
        raise OSError("Temporary failure in name resolution")

    service = PushService()
    push(seeded, service, resolve=resolve)
    assert service.pushes == [] and len(rows(seeded, PushSubscription)) == 1
    assert outbox(seeded) == [("CRITICAL", "NO_ONE")]


def test_the_pushes_go_after_the_row_is_taken_and_committed(browsers):
    """No transaction is open while a push service answers: another connection already sees the
    row taken (SENDING), and a second notifier wouldn't take it again."""
    raise_event(browsers, kind="PUMP_FAULT")
    seen = []

    def service(subscription, data, headers):
        seen.append(outbox(browsers))
        assert push(browsers, PushService()) == 0  # a second notifier, at the same time: nothing to take
        return Answer(201)

    push(browsers, service)
    assert seen == [[("CRITICAL", "SENDING")]]
    assert outbox(browsers) == [("CRITICAL", "SENT")]


def test_the_browsers_of_one_alert_are_pushed_to_at_once(browsers):
    raise_event(browsers)  # three browsers: owner, agronomist, operator
    all_there = threading.Barrier(3, timeout=5)

    def service(subscription, data, headers):
        all_there.wait()  # one browser after another would never get past this
        return Answer(201)

    push(browsers, service)
    assert outbox(browsers) == [("CRITICAL", "SENT")]
    with browsers.connect() as connection:
        assert connection.scalar(select(PushOutbox.sent)) == 3


def test_the_heartbeat_comes_after_every_row(browsers):
    raise_event(browsers)
    raise_event(browsers, zone="Z2", device="ZC2")
    beats = []
    push(browsers, PushService(), beat=lambda: beats.append(len(rows(browsers, PushOutbox))))
    assert len(beats) == 2


def test_one_slow_browser_takes_at_most_25_seconds():
    """TRIES answers of TIMEOUT_S, and two waits of MAX_WAIT_S: under the health check's 30 s."""
    assert notifier.TRIES * notifier.TIMEOUT_S + (notifier.TRIES - 1) * notifier.MAX_WAIT_S <= 25


def test_the_logs_never_hold_an_endpoint_or_its_keys(browsers, caplog):
    raise_event(browsers)
    caplog.set_level(logging.INFO)
    push(browsers, PushService(Answer(410), Answer(403)))
    assert caplog.text
    for secret in ("/fcm/send/", P256DH, AUTH):
        assert secret not in caplog.text


# --- Keys, settings and the real sender ------------------------------------------------------------


def test_a_new_key_pair_works_with_pywebpushs_own_vapid():
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid02

    public, private = keys.new_pair()
    vapid = Vapid02.from_string(private)
    raw = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    assert base64.urlsafe_b64encode(raw).rstrip(b"=").decode() == public
    assert len(raw) == 65 and keys.new_pair() != (public, private)


@pytest.mark.parametrize(("environ", "message"), [
    ({}, "VAPID_PRIVATE_KEY"),
    ({"VAPID_PRIVATE_KEY": "k"}, "VAPID_SUBJECT"),
    ({"VAPID_PRIVATE_KEY": "k", "VAPID_SUBJECT": "ops@example.org"}, "mailto"),
])
def test_the_notifier_needs_its_key_and_a_contact(environ, message):
    with pytest.raises(ValueError, match=message):
        notifier.load_vapid(environ)


def test_the_real_sender_follows_no_redirect_and_reads_the_push_services_answer(monkeypatch):
    import pywebpush
    import requests

    _, private = keys.new_pair()
    sender = notifier.WebPush(private, "mailto:ops@example.org")

    gone = requests.Response()
    gone.status_code, gone.headers["Retry-After"] = 429, "7"
    answers = iter([pywebpush.WebPushException("busy", response=gone), requests.ConnectionError("down")])

    def fake_webpush(subscription, data, **options):
        assert options["vapid_claims"] == {"sub": "mailto:ops@example.org"} and options["ttl"] == notifier.TTL_S
        session = options["requests_session"]
        assert session.max_redirects == 0 and session.trust_env is False
        assert options["timeout"] == notifier.TIMEOUT_S
        raise next(answers)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    subscription = {"endpoint": endpoint("x"), "keys": {"p256dh": P256DH, "auth": AUTH}}
    assert sender(subscription, "{}", {}) == Answer(429, 7.0)
    assert sender(subscription, "{}", {}) == Answer(0)


def test_the_health_check_follows_the_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(notifier, "HEARTBEAT", tmp_path / "alive")
    assert not notifier.healthy()
    notifier.HEARTBEAT.touch()
    assert notifier.healthy()


def test_rules_come_from_alerts_json(pack):
    rules = PushRules.from_pack(pack)
    assert rules.enabled and rules.roles_for("PUMP_FAULT") == ("OWNER",)
    assert rules.roles_for("EC_HIGH") == ("OWNER", "AGRONOMIST", "OPERATOR")
    (pack / "alerts.json").unlink()
    assert not PushRules.from_pack(pack).enabled


def test_endpoints_name_only_their_service_in_logs():
    assert endpoints.for_logs(endpoint("abc")).startswith("fcm.googleapis.com#")
    assert "abc" not in endpoints.for_logs(endpoint("abc"))
