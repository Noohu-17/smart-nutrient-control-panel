"""Playground reset: when the simulated farm was last reset, so the 24-hour totals start again.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "playground_reset",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("reset_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"],
            ["site.org_id", "site.site_id"],
            name=op.f("fk_playground_reset_org_id_site_id_site"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", name=op.f("pk_playground_reset")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("playground_reset")
