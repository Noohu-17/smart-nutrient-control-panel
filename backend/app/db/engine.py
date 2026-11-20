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
    """A database that doesn't answer fails after HEALTH_TIMEOUT_S (3 s), not libpq's default of
    waiting for ever, and a request waits at most 5 s for a free connection."""
    return create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_timeout=5,
        connect_args={"connect_timeout": max(1, round(settings.health_timeout_s))},  # libpq takes whole seconds
    )
