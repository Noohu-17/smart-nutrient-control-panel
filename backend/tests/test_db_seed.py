"""The seed loads config/clients/client_A/layout.json into the database (T1-8).

Expected values are read from layout.json itself, never typed here, so the tests keep
passing when the farm layout changes.
"""

import json
import shutil

import pytest
from sqlalchemy import select

from app.config import load_settings
from app.db.models import Bench, Device, Site, Zone
from app.db.seed import layout_rows, main, seed

CLIENT_DIR = load_settings().client_dir
MODELS = (Site, Zone, Bench, Device)


def read_layout(client_dir=CLIENT_DIR):
    return json.loads((client_dir / "layout.json").read_text(encoding="utf-8"))


# Columns the worker sets, which the seed keeps but never writes (T1-15).
WORKER_COLUMNS = {"last_seen", "last_will_at"}


def db_rows(engine, model):
    """Every row of `model` as the seed writes it, as dicts sorted by primary key."""
    table = model.__table__
    columns = [column for column in table.columns if column.name not in WORKER_COLUMNS]
    with engine.connect() as connection:
        result = connection.execute(select(*columns).order_by(*table.primary_key.columns))
        return [dict(row) for row in result.mappings()]


def expected_rows(layout, org_id, model):
    keys = [column.name for column in model.__table__.primary_key.columns]
    return sorted(layout_rows(layout, org_id)[model], key=lambda row: [row[k] for k in keys])


def test_layout_rows_take_every_value_from_layout_json():
    layout = read_layout()
    rows = layout_rows(layout, org_id="client_A")
    assert rows[Site] == [
        {"org_id": "client_A", "site_id": layout["site"], "name": layout["name"], "timezone": layout["timezone"]}
    ]
    assert [z["zone_id"] for z in rows[Zone]] == [z["id"] for z in layout["zones"]]
    assert [b["bench_id"] for b in rows[Bench]] == [b["id"] for z in layout["zones"] for b in z["benches"]]
    assert [d["device_id"] for d in rows[Device]] == [d["id"] for d in layout["devices"]]
    assert all(row["org_id"] == "client_A" and row["site_id"] == layout["site"] for m in MODELS for row in rows[m])


def test_seed_matches_layout_json(migrated_db):
    seed(migrated_db, CLIENT_DIR)
    layout = read_layout()
    for model in MODELS:
        assert db_rows(migrated_db, model) == expected_rows(layout, CLIENT_DIR.name, model), model.__name__


def test_org_id_is_the_client_folder_name(migrated_db):
    seed(migrated_db, CLIENT_DIR)
    assert {row["org_id"] for row in db_rows(migrated_db, Site)} == {CLIENT_DIR.name}


def test_seeding_twice_changes_nothing(migrated_db):
    seed(migrated_db, CLIENT_DIR)
    first = {model: db_rows(migrated_db, model) for model in MODELS}
    seed(migrated_db, CLIENT_DIR)
    assert {model: db_rows(migrated_db, model) for model in MODELS} == first


def test_the_database_follows_changes_to_layout_json(migrated_db, tmp_path):
    client_dir = shutil.copytree(CLIENT_DIR, tmp_path / "client_A")
    seed(migrated_db, client_dir)

    layout = read_layout(client_dir)
    layout["devices"] = [d for d in layout["devices"] if d["id"] != "ZC2"]
    layout["zones"][0]["benches"].pop()
    layout["zones"][0]["name"] = "Renamed zone"
    (client_dir / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    seed(migrated_db, client_dir)

    for model in MODELS:
        assert db_rows(migrated_db, model) == expected_rows(layout, "client_A", model), model.__name__
    assert "ZC2" not in {d["device_id"] for d in db_rows(migrated_db, Device)}


def test_a_broken_layout_is_a_clear_error_and_changes_nothing(migrated_db, tmp_path):
    seed(migrated_db, CLIENT_DIR)
    before = {model: db_rows(migrated_db, model) for model in MODELS}
    client_dir = shutil.copytree(CLIENT_DIR, tmp_path / "client_A")
    layout = read_layout(client_dir)
    del layout["zones"][0]["reservoir"]
    (client_dir / "layout.json").write_text(json.dumps(layout), encoding="utf-8")

    with pytest.raises(ValueError, match="reservoir"):
        seed(migrated_db, client_dir)
    assert {model: db_rows(migrated_db, model) for model in MODELS} == before


def test_the_command_line_seeds_and_reports(migrated_db, monkeypatch, capsys):
    monkeypatch.setenv("DB_NAME", migrated_db.url.database)
    assert main([str(CLIENT_DIR)]) == 0
    assert "seeded client_A/siet-ph1" in capsys.readouterr().out
    assert len(db_rows(migrated_db, Device)) == len(read_layout()["devices"])
