"""A new client's setup through the API (T1-40, contract §11.5): a draft saved step by step, each
step checked, a review, the save as a new config version, and device logins issued once.

The API here serves client_B from its pack folder; the new client is client_C on its own site,
filled in with client_B's files made client_C's.
"""

import base64
import dataclasses
import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from app.api.pack import read_farm
from app.api.routes import get_engine, get_settings
from app.config import load_settings
from app.db import config_store
from app.db.models import AuditLog, ConfigVersion, DeviceLogin, SetupDraft, Site, User
from app.db.seed import seed
from app.main import app
from app.packcheck import PACK_FILES
from app.setup import logins, routes
from app.setup.steps import STEPS
from conftest import sign_in_as

REPO = Path(__file__).resolve().parents[2]
CLIENT_B = REPO / "config" / "clients" / "client_B"
if not CLIENT_B.exists():
    pytest.skip("no config/ here (the Docker test image has none)", allow_module_level=True)

NEW_ORG, NEW_SITE = "client_C", "pune-ph1"
DRAFTS = "/api/setup/drafts"


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def db(migrated_db):
    seed(migrated_db, CLIENT_B)  # the API's own site, for its audit rows
    return migrated_db


def client(db, role: str, *, demo: bool = False) -> TestClient:
    """Signed in as `role`, a user of the API's own site with a password, or with none: a demo user
    (what POST /api/auth/demo gives)."""
    settings = dataclasses.replace(load_settings(), client_dir=CLIENT_B)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: db
    user_id = role.lower()
    with db.begin() as connection:
        site = connection.execute(select(Site.org_id, Site.site_id).where(Site.site_id == read_farm(CLIENT_B).site)).one()
        known = select(User.user_id).where(User.org_id == site.org_id, User.site_id == site.site_id,
                                           User.user_id == user_id)
        password_hash = None if demo else "$2b$12$a-password-hash-for-tests"
        if connection.scalar(known) is None:
            connection.execute(insert(User).values(
                org_id=site.org_id, site_id=site.site_id, user_id=user_id, name=f"Test {role.title()}",
                email=f"{user_id}@example.com", role=role, password_hash=password_hash,
            ))
        else:
            connection.execute(update(User).where(User.org_id == site.org_id, User.site_id == site.site_id,
                                                  User.user_id == user_id).values(password_hash=password_hash))
    sign_in_as(app, role, user_id=user_id)
    return TestClient(app, raise_server_exceptions=False)


def new_client_files() -> dict[str, dict]:
    """client_B's pack as client_C's: its own client name and its own site."""
    files = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in CLIENT_B.glob("*.json")}
    for data in files.values():
        if "client" in data.get("_meta", {}):
            data["_meta"]["client"] = NEW_ORG
    files["layout.json"]["site"] = NEW_SITE
    return files


def start(api, start_from="EMPTY", org=NEW_ORG, site=NEW_SITE):
    return api.post(DRAFTS, json={"orgId": org, "siteId": site, "startFrom": start_from})


def fill(api, draft_id, files, steps=STEPS):
    for step in steps:
        answer = api.put(f"{DRAFTS}/{draft_id}/steps/{step.id}", json={"files": {n: files[n] for n in step.files}})
        assert answer.status_code == 200, answer.text


def error(answer):
    return answer.status_code, answer.json()["error"]["code"]


def steps_of(answer) -> dict[str, dict]:
    return {step["id"]: step for step in answer.json()["steps"]}


# --- end to end ------------------------------------------------------------------------------------


def test_a_new_client_is_set_up_end_to_end(db):
    agronomist = client(db, "AGRONOMIST")
    started = start(agronomist)
    assert started.status_code == 201, started.text
    draft = started.json()
    assert (draft["orgId"], draft["siteId"], draft["status"], draft["configVersion"]) == (
        NEW_ORG, NEW_SITE, "OPEN", None)
    assert [step["id"] for step in draft["steps"]] == [step.id for step in STEPS]
    assert not any(step["filled"] for step in draft["steps"])
    draft_id = draft["id"]
    files = new_client_files()

    # The Agronomist fills in every step but the safety limits, which need the Owner.
    fill(agronomist, draft_id, files, [step for step in STEPS if step.id != "limits"])
    assert error(agronomist.put(f"{DRAFTS}/{draft_id}/steps/limits",
                                json={"files": {"limits.json": files["limits.json"]}})) == (403, "FORBIDDEN")
    review = agronomist.get(f"{DRAFTS}/{draft_id}/review").json()
    assert review["ready"] is False
    assert review["problems"] == ["limits.json: not filled in yet"]
    assert error(agronomist.post(f"{DRAFTS}/{draft_id}/save")) == (403, "FORBIDDEN")

    owner = client(db, "OWNER")
    fill(owner, draft_id, files, [step for step in STEPS if step.id == "limits"])
    review = owner.get(f"{DRAFTS}/{draft_id}/review").json()
    assert (review["ready"], review["problems"]) == (True, [])
    layout_devices = [device["id"] for device in files["layout.json"]["devices"]]
    assert review["devices"] == layout_devices

    saved = owner.post(f"{DRAFTS}/{draft_id}/save")
    assert saved.status_code == 201, saved.text
    assert saved.headers["cache-control"] == "no-store"  # it carries the passwords
    answer = saved.json()
    assert (answer["orgId"], answer["siteId"], answer["configVersion"]) == (NEW_ORG, NEW_SITE, 1)
    issued = {login["device"]: login for login in answer["deviceLogins"]}
    assert list(issued) == layout_devices
    for device, login in issued.items():
        assert login["username"] == device
        assert re.fullmatch(r"[0-9a-f]{48}", login["password"])

    # Stored as client_C's version 1, by the same import as a pack folder, and its farm loaded.
    pack = config_store.stored(db, NEW_ORG)
    assert pack.version == 1 and set(pack.files) == set(PACK_FILES)
    assert read_farm(pack).zones == read_farm(CLIENT_B).zones
    with db.connect() as connection:
        assert connection.scalar(select(ConfigVersion.imported_from).where(ConfigVersion.org_id == NEW_ORG)) == (
            f"setup {draft_id}")
        assert connection.scalar(select(Site.org_id).where(Site.site_id == NEW_SITE)) == NEW_ORG
        hashes = dict(connection.execute(
            select(DeviceLogin.device_id, DeviceLogin.password_hash).where(DeviceLogin.org_id == NEW_ORG)).all())
        audit = connection.execute(select(AuditLog.action, AuditLog.detail).order_by(AuditLog.at)).all()

    # Only each password's hash is kept, and it is the one the password makes.
    assert set(hashes) == set(layout_devices)
    for device, stored_hash in hashes.items():
        assert issued[device]["password"] not in stored_hash
        assert matches(issued[device]["password"], stored_hash)
    actions = [row.action for row in audit]
    assert actions[0] == "SETUP_STARTED" and actions[-1] == "SETUP_SAVED"
    assert actions.count("SETUP_STEP_SAVED") == len(STEPS)
    assert all(login["password"] not in json.dumps([row.detail for row in audit]) for login in issued.values())

    # Saved once: the logins are never answered again, and the draft can't change.
    assert owner.get(f"{DRAFTS}/{draft_id}").json()["status"] == "SAVED"
    assert owner.get(f"{DRAFTS}/{draft_id}").json()["configVersion"] == 1
    assert error(owner.post(f"{DRAFTS}/{draft_id}/save")) == (409, "CONFLICT")
    assert error(owner.put(f"{DRAFTS}/{draft_id}/steps/site", json={"files": {"layout.json": files["layout.json"]}})) \
        == (409, "CONFLICT")
    assert owner.get(DRAFTS).json()["items"] == []


def matches(password: str, mosquitto_hash: str) -> bool:
    _, kind, iterations, salt, key = mosquitto_hash.split("$")
    made = hashlib.pbkdf2_hmac("sha512", password.encode(), base64.b64decode(salt), int(iterations), dklen=64)
    return kind == "7" and base64.b64decode(key) == made


def test_starting_from_this_farm_copies_its_settings_as_the_new_clients(db):
    owner = client(db, "OWNER")
    draft = start(owner, "THIS_FARM").json()
    assert all(step["filled"] for step in draft["steps"])
    layout = owner.get(f"{DRAFTS}/{draft['id']}/steps/site").json()["files"]["layout.json"]
    assert layout["site"] == NEW_SITE
    limits = owner.get(f"{DRAFTS}/{draft['id']}/steps/limits").json()["files"]["limits.json"]
    assert limits["_meta"]["client"] == NEW_ORG
    assert owner.get(f"{DRAFTS}/{draft['id']}/review").json()["ready"] is True
    assert owner.post(f"{DRAFTS}/{draft['id']}/save").json()["configVersion"] == 1


# --- each step checked ----------------------------------------------------------------------------


def test_each_step_is_saved_with_its_problems_and_the_save_refuses_until_there_are_none(db):
    owner = client(db, "OWNER")
    draft_id = start(owner).json()["id"]
    files = new_client_files()

    early = owner.put(f"{DRAFTS}/{draft_id}/steps/crops", json={"files": {"recipes.json": files["recipes.json"]}})
    assert early.json()["step"]["problems"] == [
        "recipes.json: fill in the site step first; this step is checked against it"]

    wrong_site = dict(files["layout.json"], site="somewhere-else")
    site = owner.put(f"{DRAFTS}/{draft_id}/steps/site", json={"files": {"layout.json": wrong_site}}).json()
    assert site["step"]["filled"] is True
    assert any("site must be 'pune-ph1'" in problem for problem in site["step"]["problems"])

    fill(owner, draft_id, files)
    limits = json.loads(json.dumps(files["limits.json"]))
    limits["zones"].pop(max(limits["zones"]))
    answer = owner.put(f"{DRAFTS}/{draft_id}/steps/limits", json={"files": {"limits.json": limits}}).json()
    assert any("do not match the layout's zone IDs" in problem for problem in answer["step"]["problems"])
    assert answer["files"]["limits.json"] == limits  # kept, to be fixed later

    refused = owner.post(f"{DRAFTS}/{draft_id}/save")
    assert error(refused) == (422, "SETUP_NOT_READY")
    assert refused.json()["error"]["details"]["problems"] == answer["step"]["problems"]
    with db.connect() as connection:
        assert connection.scalar(select(ConfigVersion.version).where(ConfigVersion.org_id == NEW_ORG)) is None
        assert connection.scalar(select(DeviceLogin.device_id)) is None

    fill(owner, draft_id, files, [step for step in STEPS if step.id == "limits"])
    assert owner.post(f"{DRAFTS}/{draft_id}/save").status_code == 201


def test_a_step_takes_only_its_own_files(db):
    owner = client(db, "OWNER")
    draft_id = start(owner).json()["id"]
    files = new_client_files()
    assert error(owner.put(f"{DRAFTS}/{draft_id}/steps/site",
                           json={"files": {"limits.json": files["limits.json"]}})) == (422, "VALIDATION_ERROR")
    assert error(owner.put(f"{DRAFTS}/{draft_id}/steps/site", json={"files": {}})) == (422, "VALIDATION_ERROR")
    assert error(owner.put(f"{DRAFTS}/{draft_id}/steps/nope", json={"files": {}})) == (404, "NOT_FOUND")
    big = {"v": 1, "_meta": {"placeholder": True}, "padding": "x" * 300_000}
    assert error(owner.put(f"{DRAFTS}/{draft_id}/steps/users", json={"files": {"users.json": big}})) == (
        422, "VALIDATION_ERROR")


# --- who, and which clients -----------------------------------------------------------------------


@pytest.mark.parametrize("role", ["OPERATOR", "VIEWER"])
def test_only_an_owner_or_agronomist_sets_up_a_client(db, role):
    api = client(db, role)
    assert error(start(api)) == (403, "FORBIDDEN")
    assert error(api.get(DRAFTS)) == (403, "FORBIDDEN")


@pytest.mark.parametrize("role", ["OWNER", "AGRONOMIST"])
def test_a_demo_user_cant_set_up_a_client(db, role):
    """Anyone may pick a role on a demo farm, with no password: no drafts, no saves, no device logins."""
    owner = client(db, "OWNER")
    draft_id = start(owner, "THIS_FARM").json()["id"]
    demo = client(db, role, demo=True)
    draft = f"{DRAFTS}/{draft_id}"
    files = new_client_files()
    for answer in [
        start(demo, org="client_D", site="demo-ph1"),
        demo.get(DRAFTS),
        demo.get(draft),
        demo.get(f"{draft}/steps/site"),
        demo.put(f"{draft}/steps/site", json={"files": {"layout.json": files["layout.json"]}}),
        demo.get(f"{draft}/review"),
        demo.post(f"{draft}/save"),
        demo.delete(draft),
    ]:
        assert error(answer) == (403, "FORBIDDEN"), answer.request.url
    with db.connect() as connection:
        assert connection.scalar(select(ConfigVersion.version)) is None
        assert connection.scalar(select(DeviceLogin.device_id)) is None
        assert connection.scalar(select(SetupDraft.status).where(SetupDraft.draft_id == draft_id)) == "OPEN"


def test_an_import_between_the_check_and_the_save_doesnt_become_the_setup(db, monkeypatch):
    """A config_store import for the new organisation after the save's checks: 409, nothing stored."""
    owner = client(db, "OWNER")
    draft_id = start(owner, "THIS_FARM").json()["id"]
    files = {name: json.dumps(data) for name, data in new_client_files().items() if name in PACK_FILES}
    files["layout.json"] = json.dumps(dict(json.loads(files["layout.json"]), site="other-ph1"))
    config_store.import_pack(db, files, NEW_ORG)  # client_C's version 1, from somewhere else
    monkeypatch.setattr(routes, "_taken", lambda *_: None)  # as if it came in just after the check
    assert error(owner.post(f"{DRAFTS}/{draft_id}/save")) == (409, "CONFLICT")
    with db.connect() as connection:
        assert connection.scalars(select(ConfigVersion.version).where(ConfigVersion.org_id == NEW_ORG)).all() == [1]
        assert connection.scalar(select(DeviceLogin.device_id)) is None
        assert connection.scalar(select(SetupDraft.status).where(SetupDraft.draft_id == draft_id)) == "OPEN"


def test_a_client_with_settings_or_a_site_in_use_is_not_new(db):
    owner = client(db, "OWNER")
    config_store.import_folder(db, CLIENT_B)
    assert error(start(owner, org="client_B", site="another-ph1")) == (409, "CONFLICT")
    assert error(start(owner, site=read_farm(CLIENT_B).site)) == (409, "CONFLICT")  # the API's own site
    assert error(start(owner, site="Not A Slug")) == (422, "VALIDATION_ERROR")


def test_one_open_draft_per_client_until_it_is_discarded(db):
    owner = client(db, "OWNER")
    first = start(owner).json()["id"]
    assert error(start(owner)) == (409, "CONFLICT")
    assert error(start(owner, org="client_D")) == (409, "CONFLICT")  # the same site
    assert [draft["id"] for draft in owner.get(DRAFTS).json()["items"]] == [first]
    assert owner.delete(f"{DRAFTS}/{first}").status_code == 204
    assert owner.get(f"{DRAFTS}/{first}").json()["status"] == "DISCARDED"
    assert error(owner.post(f"{DRAFTS}/{first}/save")) == (409, "CONFLICT")
    assert start(owner).status_code == 201


def test_a_draft_started_on_another_site_is_not_found(db):
    with db.begin() as connection:
        connection.execute(insert(SetupDraft).values(
            draft_id="elsewhere", org_id="client_X", site_id="x-ph1", by_org_id="client_A", by_site_id="siet-ph1",
            created_by="someone", created_at=read_now(), updated_at=read_now(), status="OPEN",
        ))
    owner = client(db, "OWNER")
    assert error(owner.get(f"{DRAFTS}/elsewhere")) == (404, "NOT_FOUND")
    assert owner.get(DRAFTS).json()["items"] == []


def read_now():
    from datetime import UTC, datetime

    return datetime.now(UTC)


# --- device logins for the broker -----------------------------------------------------------------


def test_a_password_hash_is_mosquittos_own_format():
    salt = bytes(range(12))
    made = logins.mosquitto_hash("secret", salt)
    assert made.startswith("$7$101$AAECAwQFBgcICQoL$")
    assert matches("secret", made) and not matches("Secret", made)
    assert logins.mosquitto_hash("secret") != logins.mosquitto_hash("secret")  # a new salt each time


def test_the_broker_password_file_lines_come_from_the_stored_hashes(db, capsys):
    owner = client(db, "OWNER")
    draft_id = start(owner, "THIS_FARM").json()["id"]
    issued = owner.post(f"{DRAFTS}/{draft_id}/save").json()["deviceLogins"]
    assert logins.main([NEW_ORG], engine=db) == 0
    lines = capsys.readouterr().out.split()
    assert [line.partition(":")[0] for line in lines] == sorted(login["device"] for login in issued)
    by_device = {login["device"]: login["password"] for login in issued}
    assert all(matches(by_device[device], hashed) for device, _, hashed in (line.partition(":") for line in lines))
    assert logins.main(["client_none"], engine=db) == 1
