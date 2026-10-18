"""References for up to four independently encrypted attachments."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0004_attachments"
down_revision = "0003_deletions"
branch_labels = None
depends_on = None


def upgrade():
    owners = [
        ("entry_blobs", "entries", ["entry_id"], ["id"]),
        ("change_blobs", "journal_changes", ["cursor"], ["cursor"]),
        (
            "snapshot_blobs",
            "snapshot_items",
            ["snapshot_id", "entry_id"],
            ["snapshot_id", "entry_id"],
        ),
    ]
    for table, owner, local, remote in owners:
        op.create_table(
            table,
            sa.Column("user_id", UUID(), primary_key=True),
            *[
                sa.Column(name, sa.BigInteger() if name == "cursor" else UUID(), primary_key=True)
                for name in local
            ],
            sa.Column("blob_id", UUID(), primary_key=True),
            sa.ForeignKeyConstraint(
                ["user_id", *local],
                [f"{owner}.{name}" for name in ["user_id", *remote]],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["user_id", "blob_id"],
                ["blob_objects.user_id", "blob_objects.id"],
                deferrable=True,
                initially="DEFERRED",
            ),
        )
        op.create_index(f"ix_{table}_user_blob", table, ["user_id", "blob_id"])


def downgrade():
    # Downgrading with v2 data would discard file references and enable data loss.
    connection = op.get_bind()
    for owner in ("entries", "journal_changes", "snapshot_items"):
        if connection.execute(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {owner} WHERE document->>'attachment_version' = '2')"
            )
        ).scalar():
            raise RuntimeError("Restore a pre-attachment backup before downgrading v2 data")
    for table in ("snapshot_blobs", "change_blobs", "entry_blobs"):
        op.drop_table(table)
