"""Shared fixtures for the database tests.

Each test gets its own throwaway database on the dev PostgreSQL
(infra/docker-compose.dev.yml), dropped afterwards. Without that server the
database tests are skipped.
"""

import dataclasses
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from app.config import load_settings
from app.db.engine import create_db_engine

BACKEND = Path(__file__).resolve().parents[1]


def run_alembic(engine: Engine, action: str, revision: str) -> None:
    """alembic upgrade/downgrade <revision> against `engine`, as from the command line."""
    config = Config(str(BACKEND / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, action)(config, revision)


@pytest.fixture
def empty_db():
    """A new, empty database. Yields its engine; the database is dropped afterwards."""
    settings = load_settings()
    name = f"sna_test_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname="postgres",
            autocommit=True,
            connect_timeout=3,
        )
    except psycopg.OperationalError:
        pytest.skip(f"no PostgreSQL on {settings.db_host}:{settings.db_port}")
    with admin:
        admin.execute(f'CREATE DATABASE "{name}"')
        engine = create_db_engine(dataclasses.replace(settings, db_name=name))
        try:
            yield engine
        finally:
            engine.dispose()
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


@pytest.fixture
def migrated_db(empty_db):
    """empty_db after `alembic upgrade head`."""
    run_alembic(empty_db, "upgrade", "head")
    return empty_db
