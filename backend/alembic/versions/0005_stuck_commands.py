"""Stuck commands: when each sent command was last reported on, and when the worker gave up on it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-06

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sent_command", sa.Column("last_report_at", postgresql.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("sent_command", sa.Column("timed_out_at", postgresql.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sent_command", "timed_out_at")
    op.drop_column("sent_command", "last_report_at")
