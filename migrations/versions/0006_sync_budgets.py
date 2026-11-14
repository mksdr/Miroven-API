"""Backfill sync document budgets without removing content or idempotency receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0006_sync_budgets"
down_revision = "0005_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sync_states",
        sa.Column("document_bytes", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column("sync_states", sa.Column("write_window_start", sa.DateTime(timezone=True)))
    op.add_column(
        "sync_states",
        sa.Column("write_window_count", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "sync_states",
        sa.Column("write_window_bytes", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.execute("""
        UPDATE sync_states AS s SET document_bytes =
          COALESCE((SELECT SUM(octet_length(e.document::text)) FROM entries AS e
                    WHERE e.user_id = s.user_id), 0) +
          COALESCE((SELECT SUM(octet_length(c.document::text)) FROM journal_changes AS c
                    WHERE c.user_id = s.user_id), 0)
    """)
    op.create_check_constraint(
        "budgets_nonnegative",
        "sync_states",
        "document_bytes >= 0 AND write_window_count >= 0 AND write_window_bytes >= 0",
    )


def downgrade():
    op.drop_constraint(op.f("ck_sync_states_budgets_nonnegative"), "sync_states", type_="check")
    for column in (
        "write_window_bytes",
        "write_window_count",
        "write_window_start",
        "document_bytes",
    ):
        op.drop_column("sync_states", column)
