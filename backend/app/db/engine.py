"""Connecting to PostgreSQL (psycopg 3) with the DB_* settings from app.config."""

from sqlalchemy import URL, Engine, create_engine

from ..config import Settings


def database_url(settings: Settings) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
    )


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings))
