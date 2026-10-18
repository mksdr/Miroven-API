"""Durable account deletion receipts independent of user lifetime."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_deletions"
down_revision = "0002_sync"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_deletions",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(), nullable=True, unique=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'complete', 'failed')", name="status_allowed"),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )
    op.create_index(
        "ix_account_deletions_status_available", "account_deletions", ["status", "available_at"]
    )


def downgrade():
    op.drop_table("account_deletions")
