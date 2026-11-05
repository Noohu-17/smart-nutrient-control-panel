"""Dose requests: the dose_request table, and command_report for each command's newest CMD_STATE.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "command_report",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("cmd_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("boot_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("ts", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("sim_ts", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "site_id", "cmd_id", name=op.f("pk_command_report")),
    )
    op.create_index(
        op.f("ix_command_report_device_id"), "command_report", ["device_id"], unique=False
    )
    op.create_table(
        "dose_request",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("zone_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("lines", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("projected", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("can_confirm", sa.Boolean(), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "zone_id"],
            ["zone.org_id", "zone.site_id", "zone.zone_id"],
            name=op.f("fk_dose_request_org_id_site_id_zone_id_zone"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "request_id", name=op.f("pk_dose_request")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("dose_request")
    op.drop_index(op.f("ix_command_report_device_id"), table_name="command_report")
    op.drop_table("command_report")
