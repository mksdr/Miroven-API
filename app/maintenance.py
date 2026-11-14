"""Bounded retention and orphan cleanup under the same user lock as API writes."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select, union
from sqlalchemy.orm import Session

from app.models import (
    BlobObject,
    ChangeBlob,
    Entry,
    EntryBlob,
    JournalChange,
    SnapshotBlob,
    SnapshotItem,
    SyncSnapshot,
    SyncState,
    User,
)
from app.sync_budgets import document_size


def no_references():
    return [
        ~exists(
            select(1).where(
                model.user_id == BlobObject.user_id,
                model.blob_id == BlobObject.id,
            )
        )
        for model in (Entry, JournalChange, SnapshotItem, EntryBlob, ChangeBlob, SnapshotBlob)
    ]


def sweep(engine, settings, storage, batch_size=100):
    now = datetime.now(UTC)
    grace = now - timedelta(seconds=settings.blob_grace_seconds)
    cutoff = now - timedelta(days=settings.history_retention_days)
    with Session(engine) as db:
        users = list(
            db.scalars(
                union(
                    select(SyncSnapshot.user_id).where(SyncSnapshot.expires_at <= now),
                    select(JournalChange.user_id).where(JournalChange.created_at < cutoff),
                    select(BlobObject.user_id).where(
                        BlobObject.created_at < grace, *no_references()
                    ),
                ).limit(batch_size)
            )
        )
    for user_id in users:
        with Session(engine) as db, db.begin():
            user = db.scalar(
                select(User).where(User.id == user_id).with_for_update(skip_locked=True)
            )
            if user is None or user.deleting_at is not None:
                continue
            db.execute(
                delete(SyncSnapshot).where(
                    SyncSnapshot.user_id == user_id,
                    SyncSnapshot.expires_at <= now,
                )
            )
            state = db.get(SyncState, user_id)
            if state:
                rows = db.scalars(
                    select(JournalChange)
                    .where(
                        JournalChange.user_id == user_id,
                    )
                    .order_by(JournalChange.cursor)
                    .limit(1000)
                )
                floor = state.floor
                released_bytes = 0
                for row in rows:
                    if row.created_at >= cutoff:
                        break
                    floor = row.cursor
                    released_bytes += document_size(row.document)
                if floor > state.floor:
                    db.execute(
                        delete(JournalChange).where(
                            JournalChange.user_id == user_id,
                            JournalChange.cursor <= floor,
                        )
                    )
                    state.floor = floor
                    state.document_bytes -= released_bytes
            db.flush()
            blobs = db.scalars(
                select(BlobObject)
                .where(
                    BlobObject.user_id == user_id,
                    BlobObject.created_at < grace,
                    *no_references(),
                )
                .limit(100)
            )
            for blob in blobs:
                try:
                    storage.delete(blob.storage_key)
                except OSError:
                    # Keep metadata for retry, even after a partial physical delete.
                    continue
                db.delete(blob)
