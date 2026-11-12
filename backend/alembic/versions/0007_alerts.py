"""Alerts: the alert table, one row per raised condition (T1-24).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "alert",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("system_action", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("first_seen_at", TIME, nullable=False),
        sa.Column("raised_at", TIME, nullable=True),
        sa.Column("sim_ts", TIME, nullable=True),
        sa.Column("cleared_at", TIME, nullable=True),
        sa.Column("acked_at", TIME, nullable=True),
        sa.Column("acked_by", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_alert_org_id_site_id_site")
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "alert_id", name=op.f("pk_alert")),
    )
    op.create_index(op.f("ix_alert_key"), "alert", ["key"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_alert_key"), table_name="alert")
    op.drop_table("alert")
