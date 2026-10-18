"""Account subscription tiers and per-user quota overrides."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_subscriptions"
down_revision = "0004_attachments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "subscriptions",
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("plan", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("storage_quota_override_bytes", sa.BigInteger(), nullable=True),
        sa.Column("max_entries_override", sa.Integer(), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_subscription_id", sa.String(128), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("plan IN ('free', 'pro')", name=op.f("ck_subscriptions_plan_allowed")),
        sa.CheckConstraint(
            "status IN ('active', 'trialing', 'past_due', 'canceled', 'expired')",
            name=op.f("ck_subscriptions_status_allowed"),
        ),
        sa.CheckConstraint(
            "storage_quota_override_bytes IS NULL OR storage_quota_override_bytes > 0",
            name=op.f("ck_subscriptions_positive_quota_override"),
        ),
        sa.CheckConstraint(
            "max_entries_override IS NULL OR max_entries_override > 0",
            name=op.f("ck_subscriptions_positive_entries_override"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint(
            "provider_subscription_id",
            name=op.f("uq_subscriptions_provider_subscription_id"),
        ),
    )
    op.create_index("ix_subscriptions_plan_status", "subscriptions", ["plan", "status"])
    op.create_index("ix_subscriptions_period_end", "subscriptions", ["current_period_end"])


def downgrade():
    op.drop_index("ix_subscriptions_period_end", table_name="subscriptions")
    op.drop_index("ix_subscriptions_plan_status", table_name="subscriptions")
    op.drop_table("subscriptions")
