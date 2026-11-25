"""Sent commands: the sent_command table, one row for each command the API sends.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sent_command",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("cmd_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issued_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("dispatched_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dose_request_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "dose_request_id"],
            ["dose_request.org_id", "dose_request.site_id", "dose_request.request_id"],
            name=op.f("fk_sent_command_org_id_site_id_dose_request_id_dose_request"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "cmd_id", name=op.f("pk_sent_command")),
        sa.UniqueConstraint(
            "org_id", "site_id", "dose_request_id",
            name=op.f("uq_sent_command_org_id_site_id_dose_request_id"),
        ),
        sa.UniqueConstraint(
            "org_id", "site_id", "idempotency_key",
            name=op.f("uq_sent_command_org_id_site_id_idempotency_key"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sent_command")
