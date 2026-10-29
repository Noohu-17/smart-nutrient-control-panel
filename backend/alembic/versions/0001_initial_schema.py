"""Initial schema: site, zone, bench, device, app_user, and reading as a TimescaleDB hypertable.

Revision ID: 0001
Revises:
Create Date: 2026-10-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _org_site() -> list[sa.Column]:
    return [
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "site",
        *_org_site(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "site_id", name=op.f("pk_site")),
        sa.UniqueConstraint("site_id", name=op.f("uq_site_site_id")),
    )
    op.create_table(
        "zone",
        *_org_site(),
        sa.Column("zone_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("reservoir_id", sa.String(length=64), nullable=False),
        sa.Column("reservoir_usable_l", sa.Double(), nullable=False),
        sa.Column("dosing_cabinet_id", sa.String(length=64), nullable=False),
        sa.Column("dose_lines", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("day_tanks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sensors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_zone_org_id_site_id_site")
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "zone_id", name=op.f("pk_zone")),
    )
    op.create_table(
        "bench",
        *_org_site(),
        sa.Column("bench_id", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("length_m", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "zone_id"],
            ["zone.org_id", "zone.site_id", "zone.zone_id"],
            name=op.f("fk_bench_org_id_site_id_zone_id_zone"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "bench_id", name=op.f("pk_bench")),
    )
    op.create_table(
        "device",
        *_org_site(),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_device_org_id_site_id_site")
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "zone_id"],
            ["zone.org_id", "zone.site_id", "zone.zone_id"],
            name=op.f("fk_device_org_id_site_id_zone_id_zone"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "device_id", name=op.f("pk_device")),
    )
    op.create_table(
        "app_user",
        *_org_site(),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_app_user_org_id_site_id_site")
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "user_id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("org_id", "email", name=op.f("uq_app_user_org_id_email")),
    )
    op.create_table(
        "reading",
        *_org_site(),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.String(length=128), nullable=False),
        sa.Column("ts", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sim_ts", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("quality", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("org_id", "site_id", "device_id", "metric", "ts", name=op.f("pk_reading")),
    )
    op.create_index(op.f("ix_reading_ts"), "reading", ["ts"], unique=False)
    # Partition reading on ts. Our own ts index (above) replaces TimescaleDB's default one,
    # so the database stays exactly as the models describe it.
    op.execute("SELECT create_hypertable('reading', by_range('ts'), create_default_indexes => FALSE)")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("reading")  # drops the hypertable's chunks too
    op.drop_table("app_user")
    op.drop_table("device")
    op.drop_table("bench")
    op.drop_table("zone")
    op.drop_table("site")
