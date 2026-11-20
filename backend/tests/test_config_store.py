"""Client settings in the database (T1-39): import, versions, export, the app reading them, and
DEVICE_CONFIG built from them.

The tests marked with migrated_db need the dev PostgreSQL and are skipped without it. The others
use an in-memory SQLite database holding only the two config tables, or no database at all.
"""

import dataclasses
import json
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app import packcheck, packs
from app.api import device_config
from app.api.pack import read_farm
from app.api.routes import get_engine, get_settings, get_stop_pack
from app.config import DATABASE, PACK, load_settings
from app.db import config_store
from app.db import engine as engine_module
from app.db.models import ConfigFile, ConfigVersion, Site, Zone
from app.main import app
from app.packs import FolderPack, NotImported, StoredPack, load_pack
from conftest import sign_in_as

REPO = Path(__file__).resolve().parents[2]
CLIENTS = REPO / "config" / "clients"
CLIENT_A, CLIENT_B = CLIENTS / "client_A", CLIENTS / "client_B"
NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)

if not CLIENT_A.exists():
    pytest.skip("no config/ here (the Docker test image has none)", allow_module_level=True)


@pytest.fixture(autouse=True)
def fresh():
    packs.forget_kept()
    device_config.forget_started()
    yield
    app.dependency_overrides.clear()
    packs.forget_kept()
    device_config.forget_started()


def stored(folder: Path, version: int = 1) -> StoredPack:
    return StoredPack(folder.name, version, config_store.read_folder(folder))


def as_imported(folder: Path) -> dict[str, bytes]:
    """The pack's files as an export must give them back: the same bytes, plain line endings."""
    return {path.name: path.read_bytes().replace(b"\r\n", b"\n") for path in sorted(folder.glob("*.json"))}


class Broker:
    """Records what would have been published."""

    def __init__(self):
        self.sent: list[tuple[str, dict, bool]] = []

    def publish(self, topic, body, *, retain=False):
        self.sent.append((topic, body, retain))


# --- settings ------------------------------------------------------------------------------


def test_the_settings_come_from_the_pack_folder_in_dev_and_the_database_on_a_server():
    assert load_settings({}).config_source == PACK
    server = {"APP_ENV": "staging", "DB_PASSWORD": "a-real-password", "SESSION_SECRET": "x" * 40}
    assert load_settings(server).config_source == DATABASE
    assert load_settings({"CONFIG_SOURCE": "database"}).config_source == DATABASE
    assert load_settings(server | {"CONFIG_SOURCE": "pack"}).config_source == PACK
    with pytest.raises(ValueError, match="CONFIG_SOURCE"):
        load_settings({"CONFIG_SOURCE": "files"})


def test_the_organisation_is_org_id_or_the_pack_folders_name():
    assert load_settings({"CLIENT_DIR": str(CLIENT_B)}).org_id == "client_B"
    assert load_settings({"CLIENT_DIR": str(CLIENT_B), "ORG_ID": "acme"}).org_id == "acme"


# --- the same checks as scripts/validate_config.py -------------------------------------------


@pytest.mark.parametrize("folder", [CLIENT_A, CLIENT_B], ids=["client_A", "client_B"])
def test_both_packs_pass_the_checks_the_import_runs(folder):
    assert packcheck.validate_texts(folder.name, config_store.read_folder(folder)) == []


def test_a_pack_in_memory_gets_the_same_problems_as_the_same_pack_in_a_folder(tmp_path):
    folder = shutil.copytree(CLIENT_B, tmp_path / "client_B")
    limits = json.loads((folder / "limits.json").read_text(encoding="utf-8"))
    limits["zones"]["Z1"]["limits"]["maxPerDoseMl"] = {"N": 2}  # P and K left out
    (folder / "limits.json").write_text(json.dumps(limits), encoding="utf-8")
    (folder / "climate.json").unlink()

    from_folder: list[str] = []
    packcheck.validate_client(folder, from_folder)
    in_memory = packcheck.validate_texts("client_B", config_store.read_folder(folder))

    assert in_memory and len(in_memory) == len(from_folder)
    assert any("missing climate.json" in p for p in in_memory)
    assert any("limits.json" in p and "maxPerDoseMl" in p for p in in_memory)
    assert [p.partition(": ")[2] for p in in_memory] == [p.partition(": ")[2] for p in from_folder]


# --- reading a pack ----------------------------------------------------------------------------


def test_windows_line_endings_are_made_plain_and_the_digest_ignores_file_order(tmp_path):
    folder = tmp_path / "client_X"
    folder.mkdir()
    (folder / "a.json").write_bytes(b'{\r\n  "x": 1\r\n}\r\n')
    (folder / "b.json").write_bytes(b"{}\n")
    files = config_store.read_folder(folder)
    assert files == {"a.json": '{\n  "x": 1\n}\n', "b.json": "{}\n"}
    assert config_store.digest(files) == config_store.digest(dict(reversed(list(files.items()))))
    assert config_store.digest(files) != config_store.digest({**files, "b.json": "{ }\n"})


def test_a_stored_pack_reads_like_its_folder():
    folder, kept = FolderPack(CLIENT_B), stored(CLIENT_B)
    assert read_farm(kept) == read_farm(folder)
    assert kept.org_id == "client_B" and kept.version == 1
    assert kept.optional("no_such.json") is None
    with pytest.raises(FileNotFoundError):
        kept.json("no_such.json")
    kept.json("layout.json")["zones"].clear()  # a caller changing its copy changes nothing
    assert read_farm(kept).zones


# --- the app reading the database (SQLite: only the config tables) -----------------------------


@pytest.fixture
def config_db():
    # one connection, shared by the API's threads: an in-memory database lives in its connection
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    ConfigVersion.__table__.create(engine)
    ConfigFile.__table__.create(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def no_database():
    """An engine that fails if anything tries to use it."""
    return create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none", connect_args={"connect_timeout": 1})


def store(engine, folder: Path, version: int, org_id: str | None = None) -> None:
    """A version as the import stores it, without the import's Postgres-only lock and seed."""
    org_id = org_id or folder.name
    files = config_store.read_folder(folder)
    with engine.begin() as connection:
        connection.execute(
            insert(ConfigVersion).values(
                org_id=org_id, version=version, site_id="x", imported_at=NOW, digest=config_store.digest(files)
            )
        )
        connection.execute(
            insert(ConfigFile),
            [{"org_id": org_id, "version": version, "name": n, "site_id": "x", "content": t} for n, t in files.items()],
        )


def database_settings(org: str = "client_B"):
    # CLIENT_DIR names a folder that isn't there: database mode must never read files
    return dataclasses.replace(
        load_settings(), config_source=DATABASE, client_dir=Path("/nowhere") / org, org=None
    )


def test_in_database_mode_the_newest_stored_version_is_used(config_db, tmp_path):
    store(config_db, CLIENT_B, 1)
    pack = load_pack(database_settings(), config_db)
    assert isinstance(pack, StoredPack) and pack.version == 1
    assert read_farm(pack) == read_farm(CLIENT_B)

    changed = shutil.copytree(CLIENT_B, tmp_path / "client_B")
    layout = json.loads((changed / "layout.json").read_text(encoding="utf-8"))
    layout["zones"][0]["name"] = "Bay 1"
    (changed / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    store(config_db, changed, 2)
    assert read_farm(load_pack(database_settings(), config_db)).zones[0].name == "Bay 1"


def test_in_pack_mode_the_folder_is_read_and_the_database_never_asked(no_database):
    pack = load_pack(dataclasses.replace(load_settings(), client_dir=CLIENT_B), no_database)
    assert isinstance(pack, FolderPack) and pack.version is None and pack.org_id == "client_B"


def test_nothing_imported_is_said_plainly_and_never_falls_back_to_the_folder(config_db):
    settings = dataclasses.replace(database_settings(), client_dir=CLIENT_B)  # the folder is there
    with pytest.raises(NotImported, match="client_B"):
        load_pack(settings, config_db)


def test_a_database_that_stops_answering_leaves_the_version_already_loaded(config_db, monkeypatch, no_database):
    store(config_db, CLIENT_B, 1)
    settings = database_settings()
    first = load_pack(settings, config_db)

    def down(*_args):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(config_store, "latest_version", down)
    assert load_pack(settings, config_db) is first  # Stop still knows the site and zones
    with pytest.raises(packs.PackError):  # nothing kept for this database: nothing to fall back to
        load_pack(settings, no_database)


def test_stop_takes_the_version_in_memory_and_never_waits_for_the_database(config_db, monkeypatch):
    store(config_db, CLIENT_B, 1)
    settings = database_settings()
    first = load_pack(settings, config_db)
    hanging, asked = threading.Event(), threading.Event()

    def hangs(*_args):  # a database that takes the connection and never answers
        asked.set()
        hanging.wait(5)
        raise OperationalError("SELECT", {}, Exception("timeout"))

    monkeypatch.setattr(config_store, "latest_version", hangs)
    try:
        started = time.monotonic()
        assert get_stop_pack(settings, config_db) is first
        assert time.monotonic() - started < 0.5
        assert asked.wait(2)  # a newer version is still looked for, in the background
        assert get_stop_pack(settings, config_db) is first  # one look at a time
    finally:
        hanging.set()


def test_stop_sees_a_new_version_once_the_background_look_has_found_it(config_db):
    store(config_db, CLIENT_B, 1)
    settings = database_settings()
    load_pack(settings, config_db)
    store(config_db, CLIENT_B, 2)
    assert get_stop_pack(settings, config_db).version == 1  # what was in memory, at once
    for _ in range(50):
        if packs.kept_pack(settings, config_db).version == 2:
            break
        threading_wait(0.05)
    assert get_stop_pack(settings, config_db).version == 2


def test_stop_with_nothing_in_memory_and_no_database_is_a_503(no_database):
    with pytest.raises(HTTPException) as caught:
        get_stop_pack(database_settings(), no_database)
    assert caught.value.status_code == 503


def test_a_database_that_does_not_answer_fails_in_seconds(monkeypatch):
    made = {}
    monkeypatch.setattr(engine_module, "create_engine", lambda url, **kwargs: made.update(kwargs))
    engine_module.create_db_engine(dataclasses.replace(load_settings(), health_timeout_s=3))
    assert made["connect_args"] == {"connect_timeout": 3}
    assert made["pool_timeout"] == 5


@pytest.fixture
def db_api(config_db, monkeypatch):
    """The real app on config_db, signed in, with DEVICE_CONFIG sent to a recording broker."""
    broker = Broker()
    real_send_soon = device_config.send_soon
    monkeypatch.setattr(
        device_config, "send_soon", lambda settings, engine, pack: real_send_soon(settings, engine, pack, broker)
    )
    app.dependency_overrides[get_settings] = database_settings
    app.dependency_overrides[get_engine] = lambda: config_db
    sign_in_as(app, "OWNER")
    return TestClient(app, raise_server_exceptions=False), broker


def test_the_api_answers_from_the_stored_settings(db_api, config_db):
    api, _ = db_api
    store(config_db, CLIENT_B, 1)
    answer = api.get("/api/farm")
    assert answer.status_code == 200
    assert answer.json() == read_farm(CLIENT_B).model_dump(mode="json", by_alias=True, exclude_unset=True)


def test_the_api_says_when_nothing_has_been_imported(db_api):
    api, _ = db_api
    answer = api.get("/api/farm")
    assert answer.status_code == 503
    assert answer.json()["error"]["message"] == "The farm settings have not been imported into the database yet"


# --- DEVICE_CONFIG (§7.7) ----------------------------------------------------------------------


@pytest.mark.parametrize("folder", [CLIENT_A, CLIENT_B], ids=["client_A", "client_B"])
def test_each_zone_controller_gets_its_limits_with_the_stored_version(folder):
    pack = stored(folder, version=3)
    farm = read_farm(pack)
    limits = pack.json("limits.json")["zones"]
    alerts = pack.json("alerts.json")["zones"]

    configs = device_config.device_configs(pack, NOW)

    assert [c.topic for c in configs] == [f"farm/{farm.site}/device/{z.controller}/config" for z in farm.zones]
    for config, zone in zip(configs, farm.zones, strict=True):
        body = config.body
        assert {k: body[k] for k in ("v", "type", "site", "device", "configVersion", "zone")} == {
            "v": 1, "type": "DEVICE_CONFIG", "site": farm.site, "device": "CLOUD", "configVersion": 3, "zone": zone.id,
        }
        assert body["ts"] == "2026-11-19T06:00:00.000Z"
        assert body["limits"] == limits[zone.id]["limits"]
        assert body["autoRefill"] == {"enabled": False}  # features.json autoRefill is off
        wanted = {k: v for k, v in alerts[zone.id]["alarms"].items() if k in ("solTempMaxC", "circFlowMinLpm")}
        assert body["alarms"] == wanted


def test_client_a_zone_1_reads_as_the_contract_example():
    (z1, _) = device_config.device_configs(stored(CLIENT_A, version=7), NOW)
    contract = (REPO / "docs" / "contract.md").read_text(encoding="utf-8")
    example = json.loads(contract.split("### 7.7")[1].split("```json")[1].split("```")[0].replace('"…"', '"x"'))
    assert {k: z1.body[k] for k in ("configVersion", "zone", "limits", "alarms")} == {
        k: example[k] for k in ("configVersion", "zone", "limits", "alarms")
    }


def test_a_pack_folder_has_no_configversion_to_send():
    with pytest.raises(ValueError, match="configVersion"):
        device_config.device_configs(FolderPack(CLIENT_A), NOW)


def test_a_new_stored_version_is_sent_retained_once_and_recorded(db_api, config_db):
    api, broker = db_api
    store(config_db, CLIENT_B, 1)
    assert api.get("/api/farm").status_code == 200
    for _ in range(50):  # the sender runs in the background
        if broker.sent and load_pack(database_settings(), config_db).device_config_sent_at:
            break
        threading_wait(0.05)
    assert [(topic, body["configVersion"], retain) for topic, body, retain in broker.sent] == [
        (f"farm/{read_farm(CLIENT_B).site}/device/ZC1/config", 1, True),
        (f"farm/{read_farm(CLIENT_B).site}/device/ZC2/config", 1, True),
    ]
    with config_db.connect() as connection:
        assert connection.scalar(select(ConfigVersion.device_config_sent_at)) is not None

    api.get("/api/farm")
    assert len(broker.sent) == 2  # sent once per version


def test_an_older_version_never_replaces_a_newer_one(config_db):
    store(config_db, CLIENT_B, 1)
    store(config_db, CLIENT_B, 2)
    broker = Broker()
    with pytest.raises(device_config.Superseded):
        device_config.send(config_db, stored(CLIENT_B, 1), broker)
    device_config._keep_trying(config_db, stored(CLIENT_B, 1), broker, retry_s=0)  # stops, not loops
    assert broker.sent == []
    with config_db.connect() as connection:
        assert connection.scalar(select(ConfigVersion.device_config_sent_at)) is None


def test_an_older_versions_retry_stops_once_a_newer_one_is_being_sent(config_db):
    store(config_db, CLIENT_B, 1)  # the database has only 1 so far as this API knows
    broker = Broker()
    with device_config._started_lock:
        device_config._newest[(device_config._db(config_db), "client_B")] = 2
    with pytest.raises(device_config.Superseded):
        device_config.send(config_db, stored(CLIENT_B, 1), broker)
    assert broker.sent == []


def threading_wait(seconds: float) -> None:
    import threading

    threading.Event().wait(seconds)


# --- import and export (PostgreSQL) -------------------------------------------------------------


def test_client_a_and_client_b_import_from_their_packs_and_load_their_farms(migrated_db):
    a = config_store.import_folder(migrated_db, CLIENT_A)
    b = config_store.import_folder(migrated_db, CLIENT_B)
    assert (a.org_id, a.version, a.changed) == ("client_A", 1, True)
    assert (b.org_id, b.version, b.changed) == ("client_B", 1, True)
    with migrated_db.connect() as connection:
        sites = connection.execute(select(Site.org_id, Site.site_id).order_by(Site.org_id)).all()
        zones = connection.execute(select(Zone.org_id, Zone.zone_id).order_by(Zone.org_id, Zone.zone_id)).all()
    assert sites == [("client_A", read_farm(CLIENT_A).site), ("client_B", read_farm(CLIENT_B).site)]
    assert zones == [("client_A", "Z1"), ("client_A", "Z2"), ("client_B", "Z1"), ("client_B", "Z2")]


@pytest.mark.parametrize("folder", [CLIENT_A, CLIENT_B], ids=["client_A", "client_B"])
def test_export_gives_back_the_same_pack(migrated_db, tmp_path, folder):
    config_store.import_folder(migrated_db, folder)
    out = tmp_path / "exported"
    config_store.export_pack(migrated_db, folder.name, out)
    assert as_imported(out) == as_imported(folder)


def test_the_same_pack_again_changes_nothing_and_a_changed_one_is_the_next_version(migrated_db, tmp_path):
    folder = shutil.copytree(CLIENT_B, tmp_path / "client_B")
    assert config_store.import_folder(migrated_db, folder).version == 1
    again = config_store.import_folder(migrated_db, folder)
    assert (again.version, again.changed) == (1, False)

    limits = json.loads((folder / "limits.json").read_text(encoding="utf-8"))
    limits["zones"]["Z1"]["limits"]["ecCeiling"] = 1.4
    (folder / "limits.json").write_text(json.dumps(limits, indent=2), encoding="utf-8")
    changed = config_store.import_folder(migrated_db, folder)
    assert (changed.version, changed.changed) == (2, True)

    assert [row.version for row in config_store.versions(migrated_db, "client_B")] == [1, 2]
    assert config_store.export_pack(migrated_db, "client_B", tmp_path / "v1", version=1).version == 1
    assert as_imported(tmp_path / "v1") == as_imported(CLIENT_B)  # every version stays as it was
    newest = config_store.stored(migrated_db, "client_B")
    assert newest.json("limits.json")["zones"]["Z1"]["limits"]["ecCeiling"] == 1.4


def test_a_pack_with_problems_is_refused_and_nothing_is_stored(migrated_db, tmp_path):
    folder = shutil.copytree(CLIENT_B, tmp_path / "client_B")
    (folder / "nutrients.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(config_store.Refused) as refused:
        config_store.import_folder(migrated_db, folder)
    assert any("nutrients.json: not valid JSON" in p for p in refused.value.problems)
    assert config_store.versions(migrated_db, "client_B") == []
    with migrated_db.connect() as connection:
        assert connection.scalar(select(Site.site_id)) is None  # the farm isn't loaded either


def test_the_app_runs_from_the_database(migrated_db):
    config_store.import_folder(migrated_db, CLIENT_B)
    app.dependency_overrides[get_settings] = database_settings
    app.dependency_overrides[get_engine] = lambda: migrated_db
    sign_in_as(app, "OWNER")
    api = TestClient(app, raise_server_exceptions=False)

    farm = api.get("/api/farm")
    zones = api.get("/api/zones")
    limits = api.get("/api/zones/Z1/dose-limits")

    assert farm.status_code == 200 and farm.json()["site"] == read_farm(CLIENT_B).site
    assert zones.status_code == 200
    assert [z["zone"] for z in zones.json()["items"]] == ["Z1", "Z2"]
    assert zones.json()["items"][0]["profile"]["link"] == "SF_GATEWAY"  # io_map.json, from the database
    assert limits.status_code == 200
    assert [line["name"] for line in limits.json()["lines"]] == ["Nitrogen", "Phosphorus", "Potassium"]


def test_the_command_line_imports_lists_and_exports(migrated_db, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config_store, "create_db_engine", lambda _settings: migrated_db)
    monkeypatch.setattr(migrated_db, "dispose", lambda: None)
    assert config_store.main(["import", str(CLIENT_B)]) == 0
    assert config_store.main(["import", str(CLIENT_B)]) == 0
    assert config_store.main(["versions", "client_B"]) == 0
    assert config_store.main(["export", "client_B", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "stored as version 1" in printed and "unchanged, still version 1" in printed
    assert as_imported(tmp_path / "out") == as_imported(CLIENT_B)
