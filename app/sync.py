import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.cursors import open_cursor, seal_cursor
from app.errors import ApiError
from app.models import (
    BlobObject,
    ChangeBlob,
    Entry,
    EntryBlob,
    JournalChange,
    SnapshotBlob,
    SnapshotItem,
    SyncOperation,
    SyncSnapshot,
    SyncState,
    Vault,
)
from app.profile import PROFILE_DOCUMENT_ID
from app.subscriptions import effective_limits
from app.sync_schemas import ChangePage, PushAccepted, SnapshotPage, SyncChange, SyncWrite


def get_state(db: Session, user_id: uuid.UUID) -> SyncState:
    # The caller holds the user lock, including when the state row does not exist yet.
    state = db.get(SyncState, user_id)
    if state is None:
        state = SyncState(user_id=user_id)
        db.add(state)
        db.flush()
    return state


def push(db: Session, settings: Settings, user_id: uuid.UUID, body: SyncWrite) -> PushAccepted:
    if db.get(Vault, user_id) is None:
        raise ApiError(409, "vault_required", "Register the encrypted vault before synchronizing.")
    state = get_state(db, user_id)
    document = body.model_dump(mode="json")
    if body.attachment_version == 1 and not body.blob_ids:
        # Preserve pre-v2 operation fingerprints for immutable retries.
        document.pop("attachment_version")
        document.pop("blob_ids")
    fingerprint = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    previous = db.get(SyncOperation, (user_id, body.operation_id))
    if previous is not None:
        if previous.request_digest != fingerprint:
            raise ApiError(409, "operation_mismatch", "This operation ID has a different payload.")
        return PushAccepted(**previous.result)
    entry = db.get(Entry, (user_id, body.entry_id))
    current_revision = entry.revision if entry else 0
    if current_revision != body.base_revision:
        raise ApiError(409, "revision_conflict", "Another device changed this entry.")
    if entry and entry.document.get("attachment_version", 1) == 2 and body.attachment_version < 2:
        raise ApiError(409, "client_update_required", "Update the app to edit this entry.")
    limits = effective_limits(db, settings, user_id)
    if (
        entry is None
        and body.entry_id != PROFILE_DOCUMENT_ID
        and db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(
                Entry.user_id == user_id,
                Entry.id != PROFILE_DOCUMENT_ID,
            )
        )
        >= limits.entry_limit
    ):
        raise ApiError(409, "entry_limit", "The entry limit has been reached.")
    for blob_id in body.blob_ids or ([body.blob_id] if body.blob_id else []):
        blob = db.get(BlobObject, (user_id, blob_id))
        if (
            blob is None
            or blob.status != "ready"
            or blob.entry_id != body.entry_id
            or blob.revision != body.revision
        ):
            raise ApiError(409, "blob_not_ready", "Upload a matching encrypted file first.")
        blob.status = "linked"
    state.head += 1
    if entry is None:
        entry = Entry(user_id=user_id, id=body.entry_id)
        db.add(entry)
    entry.revision = body.revision
    entry.cursor = state.head
    entry.deleted = body.change_type == "delete"
    entry.blob_id = body.blob_id
    entry.document = document
    db.add(
        JournalChange(
            user_id=user_id,
            cursor=state.head,
            blob_id=body.blob_id,
            document=document,
        )
    )
    db.flush()
    db.execute(
        delete(EntryBlob).where(EntryBlob.user_id == user_id, EntryBlob.entry_id == body.entry_id)
    )
    for blob_id in body.blob_ids:
        db.add(EntryBlob(user_id=user_id, entry_id=body.entry_id, blob_id=blob_id))
        db.add(ChangeBlob(user_id=user_id, cursor=state.head, blob_id=blob_id))
    result = PushAccepted(
        revision=body.revision,
        cursor=seal_cursor(settings, user_id, state, state.head),
    )
    db.add(
        SyncOperation(
            user_id=user_id,
            id=body.operation_id,
            request_digest=fingerprint,
            result=result.model_dump(mode="json"),
        )
    )
    db.flush()
    return result


def change_view(settings, user_id, state, row) -> SyncChange:
    return SyncChange(**row.document, cursor=seal_cursor(settings, user_id, state, row.cursor))


def changes(db: Session, settings: Settings, user_id, cursor: str | None, limit: int):
    state = get_state(db, user_id)
    position = open_cursor(settings, user_id, state, cursor, "changes")["p"] if cursor else 0
    if position < state.floor:
        raise ApiError(410, "cursor_expired", "Create a snapshot to resume synchronization.")
    if position > state.head:
        raise ApiError(400, "invalid_cursor", "The cursor is invalid.")
    rows = list(
        db.scalars(
            select(JournalChange)
            .where(
                JournalChange.user_id == user_id,
                JournalChange.cursor > position,
            )
            .order_by(JournalChange.cursor)
            .limit(limit + 1)
        )
    )
    more = len(rows) > limit
    rows = rows[:limit]
    return ChangePage(
        items=[change_view(settings, user_id, state, row) for row in rows],
        next_cursor=seal_cursor(settings, user_id, state, rows[-1].cursor if rows else position),
        has_more=more,
    )


def snapshot(db: Session, settings: Settings, user_id, cursor: str | None, limit: int):
    state = get_state(db, user_id)
    now = datetime.now(UTC)
    after = None
    if cursor:
        payload = open_cursor(settings, user_id, state, cursor, "snapshot")
        record = db.get(SyncSnapshot, (user_id, uuid.UUID(payload["s"])))
        if record is None or record.expires_at <= now or record.epoch != state.epoch:
            raise ApiError(410, "snapshot_expired", "Start a new snapshot and preserve the outbox.")
        if payload["p"] != record.cursor:
            raise ApiError(400, "invalid_cursor", "The cursor is invalid.")
        after = uuid.UUID(payload["a"]) if payload["a"] else None
    else:
        db.execute(
            delete(SyncSnapshot).where(
                SyncSnapshot.user_id == user_id,
                SyncSnapshot.expires_at <= now,
            )
        )
        count = db.scalar(
            select(func.count())
            .select_from(SyncSnapshot)
            .where(
                SyncSnapshot.user_id == user_id,
            )
        )
        if count >= settings.max_snapshots:
            raise ApiError(429, "snapshot_limit", "Wait for an earlier snapshot to expire.")
        record = SyncSnapshot(
            user_id=user_id,
            epoch=state.epoch,
            cursor=state.head,
            expires_at=now + timedelta(seconds=settings.snapshot_ttl_seconds),
        )
        db.add(record)
        db.flush()
        db.execute(
            insert(SnapshotItem).from_select(
                ["user_id", "snapshot_id", "entry_id", "cursor", "blob_id", "document"],
                select(
                    Entry.user_id,
                    literal(record.id),
                    Entry.id,
                    Entry.cursor,
                    Entry.blob_id,
                    Entry.document,
                ).where(Entry.user_id == user_id),
            )
        )
        db.execute(
            insert(SnapshotBlob).from_select(
                ["user_id", "snapshot_id", "entry_id", "blob_id"],
                select(
                    EntryBlob.user_id, literal(record.id), EntryBlob.entry_id, EntryBlob.blob_id
                ).where(EntryBlob.user_id == user_id),
            )
        )
    query = select(SnapshotItem).where(
        SnapshotItem.user_id == user_id,
        SnapshotItem.snapshot_id == record.id,
    )
    if after:
        query = query.where(SnapshotItem.entry_id > after)
    rows = list(db.scalars(query.order_by(SnapshotItem.entry_id).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return SnapshotPage(
        items=[change_view(settings, user_id, state, row) for row in rows],
        next_cursor=(
            seal_cursor(settings, user_id, state, record.cursor, record.id, rows[-1].entry_id)
            if more
            else None
        ),
        has_more=more,
        resume_cursor=seal_cursor(settings, user_id, state, record.cursor),
    )
