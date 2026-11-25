"""Sign-in, sessions, roles and the audit log (T1-35, contract §11.1, §11.2, §13).

These go through the real sign-in: no conftest.sign_in_as. The client talks to https://testserver,
because the session cookie is Secure and a client only sends it over HTTPS.
"""

import base64
import dataclasses
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select, update
from test_commands import FakePublisher
from test_read_api import ORG, write_pack

from app.api.commands.routes import get_publisher
from app.api.pack import SiteRef
from app.api.routes import get_engine, get_settings
from app.auth import passwords, sessions, totp
from app.auth.access import OPEN
from app.auth.users import Refused, create_owner, create_user, reset_two_factor
from app.config import load_settings
from app.db.models import AuditLog, SentCommand, User, UserSession
from app.db.seed import seed
from app.main import app

SITE = "siet-ph1"
PASSWORD = "correct horse battery staple"
HASH = passwords.hash_password(PASSWORD)  # once: scrypt is slow on purpose
ROLES = ("OWNER", "AGRONOMIST", "OPERATOR", "VIEWER")
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)
SECRET = "a-test-session-secret-of-at-least-32-characters"
OWNER_TOTP = totp.new_secret()  # the seeded Owner's authenticator app


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pack(tmp_path):
    return write_pack(tmp_path / ORG)


@pytest.fixture
def seeded(migrated_db, pack):
    """The site, and one user per role, all with PASSWORD: owner@example.com …"""
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


@pytest.fixture
def publisher():
    return FakePublisher()


def client_on(pack, engine, publisher=None) -> TestClient:
    settings = dataclasses.replace(load_settings(), client_dir=pack)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_publisher] = lambda: publisher or FakePublisher()
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    client.engine = engine  # for sign_in's authenticator codes
    return client


@pytest.fixture
def api(seeded, pack, publisher):
    return client_on(pack, seeded, publisher)


def fresh_code(engine, user_id="owner", secret=OWNER_TOTP):
    """A code the user hasn't used yet: each one works once (totp.matching_step)."""
    with engine.connect() as connection:
        last = connection.scalar(select(User.totp_last_step).where(User.user_id == user_id))
    step = totp.step_at(datetime.now(UTC))
    return totp.code_at(secret, step if last is None or last < step else last + 1)


def sign_in(api, role="OPERATOR", password=PASSWORD):
    """Sign in as `role`, with the authenticator code for the Owner; returns the CSRF headers every
    request but a GET needs."""
    response = api.post("/api/auth/login", json={"email": f"{role.lower()}@example.com", "password": password})
    assert response.status_code == 200, response.text
    if response.json()["step"] == "TOTP":
        csrf = {"X-CSRF-Token": response.json()["csrfToken"]}
        response = api.post("/api/auth/totp", json={"code": fresh_code(api.engine)}, headers=csrf)
        assert response.status_code == 200, response.text
    assert response.json()["step"] == "DONE"
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def error(response):
    return response.status_code, response.json()["error"]["code"]


def audit_rows(engine):
    with engine.connect() as connection:
        return connection.execute(
            select(AuditLog.user_id, AuditLog.role, AuditLog.action).order_by(AuditLog.at)
        ).all()


# --- Passwords ---------------------------------------------------------------------------------


def test_a_password_is_stored_as_a_scrypt_hash_that_checks():
    assert HASH.startswith("scrypt$16384$8$5$") and PASSWORD not in HASH
    assert passwords.check_password(PASSWORD, HASH)
    assert not passwords.check_password(PASSWORD + "!", HASH)
    assert passwords.hash_password(PASSWORD) != HASH  # a new salt each time


@pytest.mark.parametrize("stored", [None, "", "plain-text", "bcrypt$x$y", "scrypt$1$2$3$!!$!!"])
def test_no_hash_or_one_it_cant_read_never_matches(stored):
    assert passwords.check_password(PASSWORD, stored) is False


def test_a_short_password_is_refused():
    with pytest.raises(ValueError, match="12"):
        passwords.hash_password("short")


# --- Session cookies and CSRF tokens -----------------------------------------------------------


def test_a_session_cookie_reads_back_until_it_expires():
    token, cookie = sessions.new_token(SECRET, "operator", NOW, hours=12)
    assert sessions.read_token(SECRET, cookie, NOW) == token
    assert token.expires_at == NOW + timedelta(hours=12)
    assert sessions.read_token(SECRET, cookie, NOW + timedelta(hours=12)) is None


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.replace(".operator.", ".owner."),  # someone else's user ID
        lambda c: c[:-2] + ("AA" if not c.endswith("AA") else "BB"),  # a changed signature
        lambda c: ".".join([*c.split(".")[:2], "9999999999", *c.split(".")[3:]]),  # a later expiry
        lambda c: ".".join([*c.split(".")[:3], "T", c.split(".")[4]]),  # another stage
        lambda c: "",
        lambda c: "not.a.cookie",
        lambda c: "a.b.c.d.e.f",
        lambda c: c + "é",  # not ASCII
    ],
    ids=["other user", "signature", "expiry", "stage", "empty", "three parts", "six parts", "not ascii"],
)
def test_a_changed_or_broken_cookie_is_refused(change):
    _, cookie = sessions.new_token(SECRET, "operator", NOW, hours=12)
    assert sessions.read_token(SECRET, change(cookie), NOW) is None


def test_a_cookie_signed_with_another_secret_is_refused():
    _, cookie = sessions.new_token(SECRET, "operator", NOW, hours=12)
    assert sessions.read_token(SECRET + "x", cookie, NOW) is None


def test_the_csrf_token_belongs_to_one_session():
    first, _ = sessions.new_token(SECRET, "operator", NOW, hours=12)
    second, _ = sessions.new_token(SECRET, "operator", NOW, hours=12)
    token = sessions.csrf_token(SECRET, first)
    assert sessions.csrf_ok(SECRET, first, token)
    assert not sessions.csrf_ok(SECRET, second, token)
    assert not sessions.csrf_ok(SECRET, first, None)
    assert not sessions.csrf_ok(SECRET, first, "é")


# --- Without signing in, no API call works -----------------------------------------------------


def _api_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/") and route.path not in OPEN:
            for method in route.methods:
                yield method, route.path.replace("{", "").replace("}", "")  # {zoneId} → zoneId


@pytest.mark.parametrize(("method", "path"), sorted(_api_routes()))
def test_every_route_but_health_and_login_is_401_without_signing_in(seeded, pack, method, path):
    response = client_on(pack, seeded).request(method, path, json={})
    assert error(response) == (401, "UNAUTHORIZED"), response.text


def test_health_and_login_need_no_session(api):
    assert api.get("/api/health").status_code != 401
    assert error(api.post("/api/auth/login", json={"email": "x@example.com", "password": "nope"})) == (
        401, "UNAUTHORIZED")


def test_an_x_demo_role_header_signs_no_one_in(api):
    response = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers={"X-Demo-Role": "Owner"})
    assert error(response) == (401, "UNAUTHORIZED")


# --- Signing in and out ------------------------------------------------------------------------


def test_signing_in_sets_a_safe_cookie_and_me_says_who(api, seeded):
    response = api.post("/api/auth/login", json={"email": " Operator@Example.com ", "password": PASSWORD})
    assert response.status_code == 200, response.text
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{sessions.COOKIE}=")
    assert {"HttpOnly", "Secure", "SameSite=lax", "Path=/api"} <= {part.strip() for part in cookie.split(";")}
    body = response.json()
    assert body["user"] == {"id": "operator", "name": "Operator Person", "email": "operator@example.com",
                            "role": "OPERATOR"}
    me = api.get("/api/me")
    assert me.status_code == 200
    assert me.json() == body
    assert audit_rows(seeded) == [("operator", "OPERATOR", "SIGN_IN")]


@pytest.mark.parametrize(
    ("email", "password"),
    [("operator@example.com", "wrong password!"), ("nobody@example.com", PASSWORD)],
    ids=["wrong password", "no such user"],
)
def test_a_wrong_email_or_password_gets_the_same_401(api, seeded, email, password):
    response = api.post("/api/auth/login", json={"email": email, "password": password})
    assert error(response) == (401, "UNAUTHORIZED")
    assert response.json()["error"]["message"] == "Wrong email or password"
    assert "set-cookie" not in response.headers
    assert [row.action for row in audit_rows(seeded)] == ["SIGN_IN_FAILED"]


def age_the_tries(engine, minutes):
    """Move every audit row `minutes` into the past, as if the tries were made that long ago."""
    with engine.begin() as connection:
        connection.execute(update(AuditLog).values(at=AuditLog.at - timedelta(minutes=minutes)))


def test_five_wrong_passwords_for_an_email_are_429_until_fifteen_minutes_pass(api, seeded):
    for _ in range(5):
        response = api.post("/api/auth/login", json={"email": "operator@example.com", "password": "wrong password!"})
        assert error(response) == (401, "UNAUTHORIZED")
    right = {"email": " Operator@Example.com ", "password": PASSWORD}  # the same email, written another way
    response = api.post("/api/auth/login", json=right)
    assert error(response) == (429, "RATE_LIMITED")
    assert 0 < int(response.headers["retry-after"]) <= 15 * 60
    assert "set-cookie" not in response.headers
    assert sign_in(api, "VIEWER")  # another email isn't held up
    age_the_tries(seeded, 14)
    assert error(api.post("/api/auth/login", json=right)) == (429, "RATE_LIMITED")
    age_the_tries(seeded, 2)
    assert api.post("/api/auth/login", json=right).status_code == 200


def test_tries_for_an_email_with_no_account_are_limited_too(api, seeded):
    for _ in range(5):
        api.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert error(api.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})) == (
        429, "RATE_LIMITED")


def test_a_user_without_a_password_cant_sign_in(api, seeded):
    with seeded.begin() as connection:
        connection.execute(update(User).where(User.user_id == "viewer").values(password_hash=None))
    assert error(api.post("/api/auth/login", json={"email": "viewer@example.com", "password": PASSWORD})) == (
        401, "UNAUTHORIZED")


def test_signing_out_ends_the_session_even_for_a_kept_copy_of_the_cookie(api, seeded):
    csrf = sign_in(api)
    kept = api.cookies[sessions.COOKIE]
    response = api.post("/api/auth/logout", headers=csrf)
    assert response.status_code == 204
    assert sessions.COOKIE not in api.cookies
    api.cookies.set(sessions.COOKIE, kept, domain="testserver", path="/api")
    assert error(api.get("/api/me")) == (401, "UNAUTHORIZED")
    assert [row.action for row in audit_rows(seeded)] == ["SIGN_IN", "SIGN_OUT"]


def test_signing_out_everywhere_ends_every_session_of_that_user(api, seeded, pack):
    other = client_on(pack, seeded)
    sign_in(other)
    csrf = sign_in(api)
    assert api.post("/api/auth/logout-all", headers=csrf).status_code == 204
    assert error(other.get("/api/me")) == (401, "UNAUTHORIZED")


def test_signing_out_needs_the_csrf_token(api):
    sign_in(api)
    assert error(api.post("/api/auth/logout")) == (403, "FORBIDDEN")
    assert api.get("/api/me").status_code == 200


def test_a_session_ends_at_its_expiry(api, seeded):
    sign_in(api)
    with seeded.begin() as connection:
        connection.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert error(api.get("/api/me")) == (401, "UNAUTHORIZED")


def test_a_session_ends_when_its_user_is_removed(api, seeded):
    sign_in(api)
    with seeded.begin() as connection:
        connection.execute(User.__table__.delete().where(User.user_id == "operator"))
    assert error(api.get("/api/me")) == (401, "UNAUTHORIZED")


# --- CSRF and roles on writes ------------------------------------------------------------------


def test_a_write_without_the_csrf_token_is_403_and_does_nothing(api, publisher):
    sign_in(api)
    assert error(api.post("/api/resume", json={"zone": "Z1"})) == (403, "FORBIDDEN")
    assert error(api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers={"X-CSRF-Token": "x"})) == (
        403, "FORBIDDEN")
    assert publisher.sent == []


def test_a_csrf_token_from_another_session_is_refused(api, seeded, pack):
    other = client_on(pack, seeded)
    stolen = sign_in(other)
    sign_in(api)
    assert error(api.post("/api/auth/logout", headers=stolen)) == (403, "FORBIDDEN")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/dose-requests", {"zone": "Z1", "lines": [{"line": "A", "ml": 10}]}),
        ("POST", "/api/dose-requests/dr_x/confirm", None),
        ("POST", "/api/resume", {"zone": "Z1"}),
        ("POST", "/api/alerts/x/ack", None),
    ],
    ids=["dose review", "dose confirm", "resume", "acknowledge"],
)
def test_a_viewer_cant_operate(api, publisher, method, path, body):
    csrf = sign_in(api, "VIEWER")
    response = api.request(method, path, json=body, headers=csrf)
    assert error(response) == (403, "FORBIDDEN"), response.text
    assert publisher.sent == []


def test_a_viewer_can_still_read(api):
    sign_in(api, "VIEWER")
    assert api.get("/api/zones").status_code == 200


@pytest.mark.parametrize("role", ROLES)
def test_every_role_may_stop_and_the_stop_says_who(api, seeded, publisher, role):
    csrf = sign_in(api, role)
    response = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers=csrf)
    assert response.status_code == 202, response.text
    [(_, command)] = publisher.sent
    assert command["issuedBy"] == f"user:{role.lower()}"
    with seeded.connect() as connection:
        assert connection.scalar(select(SentCommand.issued_by)) == role.lower()
    assert audit_rows(seeded)[-1] == (role.lower(), None, "STOP")


def test_stop_works_for_a_signed_in_user_while_the_database_is_down(api, pack, publisher):
    csrf = sign_in(api, "VIEWER")
    down = create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    app.dependency_overrides[get_engine] = lambda: down
    response = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers=csrf)
    assert response.status_code == 202, response.text
    assert [command["issuedBy"] for _, command in publisher.sent] == ["user:viewer"]
    assert error(api.get("/api/me")) == (503, "SERVICE_UNAVAILABLE")  # everything else needs the database


def test_an_operators_resume_gets_past_the_role_check(api, publisher):
    csrf = sign_in(api, "OPERATOR")
    response = api.post("/api/resume", json={"zone": "Z1"}, headers=csrf)
    assert error(response) == (409, "DEVICE_OFFLINE")  # past the role check: ZC1 has never been heard


# --- The first Owner ---------------------------------------------------------------------------


def test_create_owner_makes_an_owner_who_can_sign_in(migrated_db, pack, publisher):
    seed(migrated_db, pack)
    site = SiteRef(ORG, SITE)
    user_id, password = create_owner(migrated_db, site, " Boss@Example.org ", "The Boss")
    with migrated_db.connect() as connection:
        user = connection.execute(select(User.__table__)).one()
    assert (user.user_id, user.email, user.role, user.name) == (user_id, "boss@example.org", "OWNER", "The Boss")
    assert password not in user.password_hash and passwords.check_password(password, user.password_hash)
    assert audit_rows(migrated_db) == [(None, None, "USER_CREATED")]

    api = client_on(pack, migrated_db, publisher)
    response = api.post("/api/auth/login", json={"email": "boss@example.org", "password": password})
    assert (response.json()["user"]["role"], response.json()["step"]) == ("OWNER", "TOTP_SETUP")


def test_create_owner_refuses_a_second_owner_and_an_unseeded_site(migrated_db, pack):
    site = SiteRef(ORG, SITE)
    with pytest.raises(Refused, match="seed"):
        create_owner(migrated_db, site, "boss@example.org", "The Boss")
    seed(migrated_db, pack)
    create_owner(migrated_db, site, "boss@example.org", "The Boss")
    with pytest.raises(Refused, match="already has an Owner"):
        create_owner(migrated_db, site, "other@example.org", "Other Boss")


def test_create_user_makes_a_user_of_any_role_who_signs_in(migrated_db, pack, publisher):
    seed(migrated_db, pack)
    site = SiteRef(ORG, SITE)
    _, viewer_password = create_user(migrated_db, site, " Viewer@Example.org ", "A Viewer", "viewer")
    create_owner(migrated_db, site, "boss@example.org", "The Boss")
    _, second = create_user(migrated_db, site, "second@example.org", "Second Owner", "OWNER")  # more than one
    with migrated_db.connect() as connection:
        roles = dict(connection.execute(select(User.email, User.role)).all())
    assert roles == {"viewer@example.org": "VIEWER", "boss@example.org": "OWNER", "second@example.org": "OWNER"}

    api = client_on(pack, migrated_db, publisher)
    response = api.post("/api/auth/login", json={"email": "viewer@example.org", "password": viewer_password})
    assert (response.json()["user"]["role"], response.json()["step"]) == ("VIEWER", "DONE")
    response = client_on(pack, migrated_db).post(
        "/api/auth/login", json={"email": "second@example.org", "password": second})
    assert response.json()["step"] == "TOTP_SETUP"  # features.json leaves ownerTwoFactor out: two-factor


def test_create_user_refuses_a_bad_role_a_taken_email_and_an_unseeded_site(migrated_db, pack):
    site = SiteRef(ORG, SITE)
    with pytest.raises(Refused, match="seed"):
        create_user(migrated_db, site, "a@example.org", "A", "OPERATOR")
    seed(migrated_db, pack)
    with pytest.raises(Refused, match="role must be one of"):
        create_user(migrated_db, site, "a@example.org", "A", "BOSS")
    create_user(migrated_db, site, "a@example.org", "A", "OPERATOR")
    with pytest.raises(Refused, match="already has an account"):
        create_user(migrated_db, site, "A@example.org", "Again", "VIEWER")


def test_an_owner_signs_in_with_the_password_only_when_owner_two_factor_is_off(migrated_db, tmp_path, publisher):
    """features.json "ownerTwoFactor": false (client_B while it is shown and tested)."""
    pack = write_pack(tmp_path / ORG, features={"playground": False, "ownerTwoFactor": False})
    seed(migrated_db, pack)
    _, password = create_owner(migrated_db, SiteRef(ORG, SITE), "boss@example.org", "The Boss")
    api = client_on(pack, migrated_db, publisher)
    response = api.post("/api/auth/login", json={"email": "boss@example.org", "password": password})
    assert (response.json()["user"]["role"], response.json()["step"]) == ("OWNER", "DONE")
    assert api.get("/api/users").status_code == 200  # signed in fully, no code


def test_an_owner_who_set_two_factor_up_keeps_it_when_the_switch_is_off(migrated_db, tmp_path, publisher):
    pack = write_pack(tmp_path / ORG, features={"playground": False, "ownerTwoFactor": False})
    seed(migrated_db, pack)
    user_id, password = create_owner(migrated_db, SiteRef(ORG, SITE), "boss@example.org", "The Boss")
    with migrated_db.begin() as connection:
        connection.execute(update(User).where(User.user_id == user_id).values(
            totp_secret=OWNER_TOTP, totp_enabled_at=NOW))
    response = client_on(pack, migrated_db, publisher).post(
        "/api/auth/login", json={"email": "boss@example.org", "password": password})
    assert response.json()["step"] == "TOTP"


# --- Demo sign-in -----------------------------------------------------------------------------


def demo_pack(folder, **features):
    """A pack with client_A's demo users and the given feature switches."""
    write_pack(folder, features={"playground": True, "demoFarm": True, **features})
    shutil.copy(load_settings().client_dir / "users.json", folder / "users.json")
    return folder


@pytest.mark.parametrize("role", ROLES)
def test_demo_sign_in_signs_in_as_that_roles_demo_user(migrated_db, tmp_path, publisher, role):
    pack = demo_pack(tmp_path / ORG)
    seed(migrated_db, pack)
    api = client_on(pack, migrated_db, publisher)
    response = api.post("/api/auth/demo", json={"role": role})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == f"demo-{role.lower()}"
    assert response.json()["user"]["role"] == role
    assert api.get("/api/me").json()["user"]["role"] == role
    stop = api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers={"X-CSRF-Token": response.json()["csrfToken"]})
    assert stop.status_code == 202
    assert audit_rows(migrated_db)[0] == (f"demo-{role.lower()}", role, "SIGN_IN_DEMO")


def test_a_demo_user_keeps_its_role_checks_and_has_no_password(migrated_db, tmp_path, publisher):
    pack = demo_pack(tmp_path / ORG)
    seed(migrated_db, pack)
    api = client_on(pack, migrated_db, publisher)
    csrf = {"X-CSRF-Token": api.post("/api/auth/demo", json={"role": "VIEWER"}).json()["csrfToken"]}
    assert error(api.post("/api/resume", json={"zone": "Z1"}, headers=csrf)) == (403, "FORBIDDEN")
    api.post("/api/auth/demo", json={"role": "VIEWER"})  # again: the same user, not a second one
    with migrated_db.connect() as connection:
        assert connection.execute(select(User.user_id, User.password_hash)).all() == [("demo-viewer", None)]


@pytest.mark.parametrize(
    "features", [{"demoFarm": False}, {"playground": False}], ids=["not a demo farm", "not a playground"]
)
def test_demo_sign_in_is_404_unless_the_site_is_a_demo_playground(migrated_db, tmp_path, features):
    pack = demo_pack(tmp_path / ORG, **features)
    seed(migrated_db, pack)
    response = client_on(pack, migrated_db).post("/api/auth/demo", json={"role": "OWNER"})
    assert error(response) == (404, "NOT_FOUND")
    assert "set-cookie" not in response.headers


def test_demo_sign_in_takes_only_the_four_roles(api):
    assert error(api.post("/api/auth/demo", json={"role": "ENGINEER"})) == (422, "VALIDATION_ERROR")


# --- Two-factor for the Owner ------------------------------------------------------------------


def owner_password_step(api):
    """The Owner's password step only: the CSRF headers of a session waiting for the code."""
    response = api.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert response.json()["step"] == "TOTP", response.text
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def test_an_owner_needs_the_code_and_until_then_can_do_nothing_else(api, seeded, publisher):
    csrf = owner_password_step(api)
    assert api.get("/api/me").json()["step"] == "TOTP"
    assert error(api.get("/api/zones")) == (403, "TWO_FACTOR_REQUIRED")
    assert error(api.post("/api/stop", json={"scope": "ALL_OPERATIONS"}, headers=csrf)) == (403, "TWO_FACTOR_REQUIRED")
    assert publisher.sent == []
    response = api.post("/api/auth/totp", json={"code": fresh_code(seeded)}, headers=csrf)
    assert response.status_code == 200 and response.json()["step"] == "DONE"
    assert api.get("/api/zones").status_code == 200
    assert [row.action for row in audit_rows(seeded)] == ["SIGN_IN_PASSWORD", "SIGN_IN"]


def test_the_code_gives_a_new_session_and_the_one_before_it_is_worthless(api, seeded):
    csrf = owner_password_step(api)
    before = api.cookies[sessions.COOKIE]
    api.post("/api/auth/totp", json={"code": fresh_code(seeded)}, headers=csrf)
    assert api.cookies[sessions.COOKIE] != before
    api.cookies.clear()
    api.cookies.set(sessions.COOKIE, before, domain="testserver", path="/api")
    assert error(api.get("/api/me")) == (401, "UNAUTHORIZED")


def test_a_code_works_once(api, seeded, pack):
    csrf = owner_password_step(api)
    code = fresh_code(seeded)
    assert api.post("/api/auth/totp", json={"code": code}, headers=csrf).status_code == 200
    other = client_on(pack, seeded)
    other_csrf = owner_password_step(other)
    assert error(other.post("/api/auth/totp", json={"code": code}, headers=other_csrf)) == (400, "WRONG_CODE")


def test_five_wrong_codes_end_the_session(api, seeded):
    csrf = owner_password_step(api)
    for _ in range(4):
        assert error(api.post("/api/auth/totp", json={"code": "000000"}, headers=csrf)) == (400, "WRONG_CODE")
    assert error(api.post("/api/auth/totp", json={"code": "000000"}, headers=csrf)) == (401, "UNAUTHORIZED")
    assert error(api.post("/api/auth/totp", json={"code": fresh_code(seeded)}, headers=csrf)) == (401, "UNAUTHORIZED")


def test_wrong_codes_count_over_every_session_so_a_new_sign_in_is_429(api, seeded):
    for wrong_codes in (3, 2):  # 3 wrong codes, then 2 in a new session: 5
        csrf = owner_password_step(api)
        for _ in range(wrong_codes):
            assert error(api.post("/api/auth/totp", json={"code": "000000"}, headers=csrf)) == (400, "WRONG_CODE")
    csrf = owner_password_step(api)  # the password is right, so this step still works
    response = api.post("/api/auth/totp", json={"code": fresh_code(seeded)}, headers=csrf)
    assert error(response) == (429, "RATE_LIMITED") and "retry-after" in response.headers
    age_the_tries(seeded, 16)
    assert api.post("/api/auth/totp", json={"code": fresh_code(seeded)}, headers=csrf).status_code == 200


def test_the_code_needs_the_csrf_token_and_a_full_session_needs_no_code(api, seeded):
    owner_password_step(api)
    assert error(api.post("/api/auth/totp", json={"code": fresh_code(seeded)})) == (403, "FORBIDDEN")
    csrf = sign_in(api, "OPERATOR")
    assert error(api.post("/api/auth/totp", json={"code": "123456"}, headers=csrf)) == (409, "CONFLICT")


def test_a_new_owner_sets_up_two_factor_before_anything_else(migrated_db, pack, publisher):
    seed(migrated_db, pack)
    _, password = create_owner(migrated_db, SiteRef(ORG, SITE), "boss@example.org", "The Boss")
    api = client_on(pack, migrated_db, publisher)
    response = api.post("/api/auth/login", json={"email": "boss@example.org", "password": password})
    csrf = {"X-CSRF-Token": response.json()["csrfToken"]}
    assert response.json()["step"] == "TOTP_SETUP"
    assert error(api.get("/api/users")) == (403, "TWO_FACTOR_REQUIRED")
    assert error(api.post("/api/auth/totp", json={"code": "123456"}, headers=csrf)) == (409, "CONFLICT")
    setup = api.post("/api/auth/totp", json={}, headers=csrf).json()
    assert setup["otpauthUri"].startswith("otpauth://totp/") and f"secret={setup['secret']}" in setup["otpauthUri"]
    assert error(api.post("/api/auth/totp", json={"code": "000000"}, headers=csrf)) == (400, "WRONG_CODE")
    code = totp.code_at(setup["secret"], totp.step_at(datetime.now(UTC)))
    response = api.post("/api/auth/totp", json={"code": code}, headers=csrf)
    assert response.status_code == 200 and response.json()["step"] == "DONE"
    assert api.get("/api/users").status_code == 200
    again = client_on(pack, migrated_db).post("/api/auth/login", json={"email": "boss@example.org", "password": password})
    assert again.json()["step"] == "TOTP"  # two-factor is on from now


def test_a_demo_owner_needs_no_code(migrated_db, tmp_path):
    pack = demo_pack(tmp_path / ORG)
    seed(migrated_db, pack)
    assert client_on(pack, migrated_db).post("/api/auth/demo", json={"role": "OWNER"}).json()["step"] == "DONE"


def test_reset_two_factor_turns_it_off_and_signs_the_user_out(api, seeded, pack):
    sign_in(api, "OWNER")
    reset_two_factor(seeded, SiteRef(ORG, SITE), "Owner@Example.com")
    assert error(api.get("/api/me")) == (401, "UNAUTHORIZED")
    response = client_on(pack, seeded).post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert response.json()["step"] == "TOTP_SETUP"
    with pytest.raises(Refused, match="no user"):
        reset_two_factor(seeded, SiteRef(ORG, SITE), "nobody@example.com")


def test_a_code_matches_the_rfc_6238_test_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()  # RFC 6238, appendix B, SHA-1
    assert totp.code_at(secret, 59 // 30) == "287082"  # the RFC's 94287082, to 6 digits
    assert totp.code_at(secret, 1111111109 // 30) == "081804"


# --- Users and roles (Owner only) --------------------------------------------------------------


def test_only_an_owner_manages_users(api, seeded):
    for role in ("AGRONOMIST", "OPERATOR", "VIEWER"):
        csrf = sign_in(api, role)
        assert error(api.get("/api/users")) == (403, "FORBIDDEN")
        new = {"name": "X", "email": "x@example.com", "role": "VIEWER"}
        assert error(api.post("/api/users", json=new, headers=csrf)) == (403, "FORBIDDEN")
        assert error(api.patch("/api/users/viewer", json={"role": "OWNER"}, headers=csrf)) == (403, "FORBIDDEN")
        assert error(api.post("/api/users/viewer/invite", headers=csrf)) == (403, "FORBIDDEN")


def test_an_owner_lists_adds_and_invites_a_user_who_then_signs_in(api, seeded, pack):
    csrf = sign_in(api, "OWNER")
    assert [u["id"] for u in api.get("/api/users").json()["items"]] == ["agronomist", "operator", "owner", "viewer"]
    new = {"name": "Nina New", "email": " Nina@Example.com ", "role": "OPERATOR"}
    created = api.post("/api/users", json=new, headers=csrf)
    assert created.status_code == 201, created.text
    user = created.json()
    assert (user["email"], user["role"], user["active"], user["hasPassword"], user["twoFactor"]) == (
        "nina@example.com", "OPERATOR", True, False, False)
    invite = api.post(f"/api/users/{user['id']}/invite", headers=csrf).json()
    assert invite["user"]["hasPassword"] is True and len(invite["password"]) >= 12
    nina = client_on(pack, seeded)
    response = nina.post("/api/auth/login", json={"email": "nina@example.com", "password": invite["password"]})
    assert (response.json()["user"]["role"], response.json()["step"]) == ("OPERATOR", "DONE")
    assert [row.action for row in audit_rows(seeded)][-3:] == ["USER_CREATED", "USER_INVITED", "SIGN_IN"]


def test_an_email_is_used_once_and_bad_input_is_refused(api, seeded):
    csrf = sign_in(api, "OWNER")
    taken = {"name": "Again", "email": "OPERATOR@example.com", "role": "VIEWER"}
    assert error(api.post("/api/users", json=taken, headers=csrf)) == (409, "CONFLICT")
    for body in ({"name": "X", "email": "not-an-email", "role": "VIEWER"},
                 {"name": "X", "email": "x@example.com", "role": "ENGINEER"},
                 {"name": "", "email": "x@example.com", "role": "VIEWER"}):
        assert error(api.post("/api/users", json=body, headers=csrf)) == (422, "VALIDATION_ERROR")
    assert error(api.patch("/api/users/nobody", json={"name": "X"}, headers=csrf)) == (404, "NOT_FOUND")


def test_a_new_role_applies_at_once_and_signs_the_user_out(api, seeded, pack):
    viewer = client_on(pack, seeded)
    sign_in(viewer, "VIEWER")
    csrf = sign_in(api, "OWNER")
    changed = api.patch("/api/users/viewer", json={"role": "OPERATOR", "name": "Vic"}, headers=csrf).json()
    assert (changed["role"], changed["name"]) == ("OPERATOR", "Vic")
    assert error(viewer.get("/api/me")) == (401, "UNAUTHORIZED")
    viewer_csrf = sign_in(viewer, "VIEWER")  # the same email: now an Operator
    assert viewer.get("/api/me").json()["user"]["role"] == "OPERATOR"
    assert error(viewer.post("/api/resume", json={"zone": "Z1"}, headers=viewer_csrf)) == (409, "DEVICE_OFFLINE")


def test_a_turned_off_user_cant_sign_in_until_turned_on(api, seeded, pack):
    operator = client_on(pack, seeded)
    sign_in(operator, "OPERATOR")
    csrf = sign_in(api, "OWNER")
    assert api.patch("/api/users/operator", json={"active": False}, headers=csrf).json()["active"] is False
    assert error(operator.get("/api/me")) == (401, "UNAUTHORIZED")
    login = {"email": "operator@example.com", "password": PASSWORD}
    assert error(operator.post("/api/auth/login", json=login)) == (401, "UNAUTHORIZED")
    assert error(api.post("/api/users/operator/invite", headers=csrf)) == (409, "CONFLICT")
    api.patch("/api/users/operator", json={"active": True}, headers=csrf)
    assert operator.post("/api/auth/login", json=login).status_code == 200


def test_the_last_active_owner_cant_be_turned_off_or_given_another_role(api, seeded):
    csrf = sign_in(api, "OWNER")
    for change in ({"active": False}, {"role": "AGRONOMIST"}):
        assert error(api.patch("/api/users/owner", json=change, headers=csrf)) == (409, "LAST_OWNER")
    api.patch("/api/users/agronomist", json={"role": "OWNER"}, headers=csrf)
    assert api.patch("/api/users/owner", json={"role": "AGRONOMIST"}, headers=csrf).json()["role"] == "AGRONOMIST"
