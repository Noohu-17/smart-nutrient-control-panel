"""Web Push for alerts: push_subscription, the browsers that turned notifications on, and
push_outbox, the alerts the notifier still has to push (T1-41).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIME = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "push_outbox",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("queued_at", TIME, nullable=False),
        sa.Column("done_at", TIME, nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("sent", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "alert_id"],
            ["alert.org_id", "alert.site_id", "alert.alert_id"],
            name=op.f("fk_push_outbox_org_id_site_id_alert_id_alert"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "alert_id", "severity", name=op.f("pk_push_outbox")),
    )
    op.create_index(op.f("ix_push_outbox_queued_at"), "push_outbox", ["queued_at"], unique=False)
    op.create_table(
        "push_subscription",
        sa.Column("org_id", sa.String(length=64), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("subscription_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=128), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("severities", sa.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("created_at", TIME, nullable=False),
        sa.Column("updated_at", TIME, nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "site_id", "user_id"],
            ["app_user.org_id", "app_user.site_id", "app_user.user_id"],
            name=op.f("fk_push_subscription_org_id_site_id_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "site_id", "subscription_id", name=op.f("pk_push_subscription")),
        sa.UniqueConstraint("org_id", "site_id", "endpoint", name=op.f("uq_push_subscription_org_id_site_id_endpoint")),
    )
    op.create_index(op.f("ix_push_subscription_user_id"), "push_subscription", ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_push_subscription_user_id"), table_name="push_subscription")
    op.drop_table("push_subscription")
    op.drop_index(op.f("ix_push_outbox_queued_at"), table_name="push_outbox")
    op.drop_table("push_outbox")
