"""Client settings in the database: config_version and config_file, each organisation's config
pack, versioned (T1-39).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "config_version",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("imported_at", TIME, nullable=False),
        sa.Column("imported_from", sa.Text(), nullable=True),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("device_config_sent_at", TIME, nullable=True),
        sa.PrimaryKeyConstraint("org_id", "version", name=op.f("pk_config_version")),
    )
    op.create_table(
        "config_file",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "version"],
            ["config_version.org_id", "config_version.version"],
            name=op.f("fk_config_file_org_id_version_config_version"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "version", "name", name=op.f("pk_config_file")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("config_file")
    op.drop_table("config_version")
