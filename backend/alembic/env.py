"""Alembic environment: migrations run against the database in the DB_* settings (app/config.py).

Tests pass their own connection in config.attributes["connection"].
"""

from logging.config import fileConfig

from alembic import context

from app.config import load_settings
from app.db.engine import create_db_engine, database_url
from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():  # alembic upgrade head --sql: print the SQL instead
    context.configure(
        url=database_url(load_settings()).render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    engine = create_db_engine(load_settings())
    with engine.connect() as connection:
        run_migrations(connection)
    engine.dispose()
