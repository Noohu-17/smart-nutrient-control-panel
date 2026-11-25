"""Latest state: the latest_message table, and device.last_seen and device.last_will_at.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("device", sa.Column("last_seen", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("device", sa.Column("last_will_at", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.create_table(
        "latest_message",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=True),
        sa.Column("boot_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("ts", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sim_ts", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint(
            "org_id", "site_id", "device_id", "type", name=op.f("pk_latest_message")
        ),
    )
    # A device that reported before this migration keeps its last-seen time.
    op.execute(
        """
        UPDATE device AS d SET last_seen = (
            SELECT max(r.ts) FROM reading AS r
            WHERE r.org_id = d.org_id AND r.site_id = d.site_id AND r.device_id = d.device_id
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("latest_message")
    op.drop_column("device", "last_will_at")
    op.drop_column("device", "last_seen")
