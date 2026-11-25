"""Stock grades: stock_grade, each dose line's N-P-K as an Owner or Agronomist set it in the app,
over the config pack's nutrients.json npk.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-25

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "stock_grade",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("line", sa.String(length=16), nullable=False),
        sa.Column("n", sa.Float(), nullable=False),
        sa.Column("p", sa.Float(), nullable=False),
        sa.Column("k", sa.Float(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=False),
        sa.Column("updated_at", TIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_stock_grade_org_id_site_id_site")
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "line", name=op.f("pk_stock_grade")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("stock_grade")
