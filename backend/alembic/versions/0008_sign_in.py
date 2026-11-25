"""Sign-in: user_session and audit_log, two-factor and turned-off users in app_user, and who did it
on dose requests, sent commands and alerts (T1-35).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-19

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_session",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", TIME, nullable=False),
        sa.Column("expires_at", TIME, nullable=False),
        sa.Column("ended_at", TIME, nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("failed_codes", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "user_id"],
            ["app_user.org_id", "app_user.site_id", "app_user.user_id"],
            name=op.f("fk_user_session_org_id_site_id_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "session_hash", name=op.f("pk_user_session")),
    )
    op.create_index(op.f("ix_user_session_user_id"), "user_session", ["user_id"], unique=False)

    op.create_table(
        "audit_log",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("audit_id", sa.String(length=64), nullable=False),
        sa.Column("at", TIME, nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id"], ["site.org_id", "site.site_id"], name=op.f("fk_audit_log_org_id_site_id_site")
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "audit_id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_at"), "audit_log", ["at"], unique=False)

    op.add_column("app_user", sa.Column("totp_secret", sa.Text(), nullable=True))
    op.add_column("app_user", sa.Column("totp_enabled_at", TIME, nullable=True))
    op.add_column("app_user", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    op.add_column("app_user", sa.Column("disabled_at", TIME, nullable=True))
    op.add_column("dose_request", sa.Column("requested_by", sa.String(length=64), nullable=True))
    op.add_column("sent_command", sa.Column("issued_by", sa.String(length=64), nullable=True))
    op.alter_column(
        "alert", "acked_by", existing_type=sa.String(length=32), type_=sa.String(length=64), existing_nullable=True
    )


def downgrade() -> None:
    """Downgrade schema. A user ID longer than 32 characters in alert.acked_by is cut to 32."""
    op.execute("UPDATE alert SET acked_by = left(acked_by, 32)")
    op.alter_column(
        "alert", "acked_by", existing_type=sa.String(length=64), type_=sa.String(length=32), existing_nullable=True
    )
    op.drop_column("sent_command", "issued_by")
    op.drop_column("dose_request", "requested_by")
    for column in ("disabled_at", "totp_last_step", "totp_enabled_at", "totp_secret"):
        op.drop_column("app_user", column)
    op.drop_index(op.f("ix_audit_log_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_user_session_user_id"), table_name="user_session")
    op.drop_table("user_session")
