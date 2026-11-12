"""The first Alembic migration builds the database the models describe (T1-8)."""

from datetime import UTC, datetime

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Base, Reading
from conftest import run_alembic

TABLES = {
    "site", "zone", "bench", "device", "reading", "latest_message", "command_report", "dose_request", "app_user",
    "sent_command", "playground_reset", "alert",
}


def test_upgrade_head_builds_every_table(migrated_db):
    assert set(inspect(migrated_db).get_table_names()) == TABLES | {"alembic_version"}


def test_every_table_has_org_id_and_site_id(migrated_db):
    inspector = inspect(migrated_db)
    for table in TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert {"org_id", "site_id"} <= set(columns), table
        assert not columns["org_id"]["nullable"] and not columns["site_id"]["nullable"], table


def test_the_migration_matches_the_models(migrated_db):
    with migrated_db.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert differences == []


def test_reading_is_a_timescaledb_hypertable_on_ts(migrated_db):
    with migrated_db.connect() as connection:
        dimensions = connection.execute(
            text(
                "SELECT column_name FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'reading'"
            )
        ).scalars().all()
    assert dimensions == ["ts"]


def test_readings_store_null_for_a_failed_sensor_and_drop_replays(migrated_db):
    row = {
        "org_id": "client_A", "site_id": "siet-ph1", "device_id": "ZC1", "zone_id": "Z1",
        "metric": "ec", "ts": datetime(2026, 11, 9, 4, 54, tzinfo=UTC), "value": None,
        "quality": "FAULT",
    }
    with migrated_db.begin() as connection:
        for _ in range(2):  # the same message twice, as after a gateway replay
            connection.execute(insert(Reading).values(row).on_conflict_do_nothing())
        stored = connection.execute(select(Reading.value, Reading.quality)).all()
    assert stored == [(None, "FAULT")]


def test_downgrade_removes_every_table(migrated_db):
    run_alembic(migrated_db, "downgrade", "base")
    assert set(inspect(migrated_db).get_table_names()) == {"alembic_version"}
