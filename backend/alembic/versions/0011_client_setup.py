"""Client setup: setup_draft and setup_draft_file, a new client's settings filled in step by step,
and device_login, the farm devices' MQTT logins issued when the setup is saved (T1-40).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)
ID = sa.String(length=64)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "setup_draft",
        sa.Column("draft_id", ID, nullable=False),
        sa.Column("org_id", ID, nullable=False),
        sa.Column("site_id", ID, nullable=False),
        sa.Column("by_org_id", ID, nullable=False),
        sa.Column("by_site_id", ID, nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column("created_at", TIME, nullable=False),
        sa.Column("updated_at", TIME, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("saved_version", sa.Integer(), nullable=True),
        sa.Column("saved_at", TIME, nullable=True),
        sa.PrimaryKeyConstraint("draft_id", name=op.f("pk_setup_draft")),
    )
    # One open draft per new organisation and per new site: two people can't set up one client at once.
    for column in ("org_id", "site_id"):
        op.create_index(
            f"uq_setup_draft_open_{column.removesuffix('_id')}", "setup_draft", [column], unique=True,
            postgresql_where=sa.text("status = 'OPEN'"),
        )
    op.create_table(
        "setup_draft_file",
        sa.Column("draft_id", ID, nullable=False),
        sa.Column("name", ID, nullable=False),
        sa.Column("org_id", ID, nullable=False),
        sa.Column("site_id", ID, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_by", ID, nullable=False),
        sa.Column("updated_at", TIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["setup_draft.draft_id"],
            name=op.f("fk_setup_draft_file_draft_id_setup_draft"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("draft_id", "name", name=op.f("pk_setup_draft_file")),
    )
    op.create_table(
        "device_login",
        sa.Column("org_id", ID, nullable=False),
        sa.Column("site_id", ID, nullable=False),
        sa.Column("device_id", ID, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("issued_at", TIME, nullable=False),
        sa.Column("issued_by", ID, nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"],
            name=op.f("fk_device_login_org_id_site_id_site"),
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "device_id", name=op.f("pk_device_login")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("device_login")
    op.drop_table("setup_draft_file")
    op.drop_index("uq_setup_draft_open_site", table_name="setup_draft")
    op.drop_index("uq_setup_draft_open_org", table_name="setup_draft")
    op.drop_table("setup_draft")
