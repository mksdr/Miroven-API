import hashlib
import uuid
from typing import BinaryIO

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import check_csrf, principal
from app.errors import ApiError
from app.models import BlobObject, Vault
from app.profile import require_journal_blob
from app.storage import ObjectStorage, build_storage
from app.subscriptions import effective_limits
from app.sync_schemas import BlobReceipt


def storage_for(request: Request) -> ObjectStorage:
    if request.app.state.storage is None:
        request.app.state.storage = build_storage(request.app.state.settings)
    return request.app.state.storage


def upload_identity(request: Request, db: Session):
    user, _session, token = principal(request, db)
    check_csrf(request, token)
    if db.get(Vault, user.id) is None:
        raise ApiError(409, "vault_required", "Register the encrypted vault before uploading.")
    return user.id


def authorize_upload(request: Request, db: Session, entry_id: uuid.UUID):
    user_id = upload_identity(request, db)
    require_journal_blob(entry_id)
    limits = effective_limits(db, request.app.state.settings, user_id)
    request.state.blob_max_bytes = limits.blob_max_bytes
    # Do not keep a DB connection or user lock while a slow client sends its body.
    # save_blob re-authenticates before reservation and before the storage write.
    db.rollback()


def save_blob(
    request: Request,
    db: Session,
    blob_id: uuid.UUID,
    entry_id: uuid.UUID,
    revision: int,
    source: BinaryIO,
    size: int,
    checksum: str,
) -> BlobReceipt:
    user_id = upload_identity(request, db)
    require_journal_blob(entry_id)
    settings = request.app.state.settings
    limits = effective_limits(db, settings, user_id)
    if size > limits.blob_max_bytes:
        raise ApiError(413, "request_too_large", "The encrypted file is too large.")
    blob = db.get(BlobObject, (user_id, blob_id))
    if blob is not None:
        if (blob.sha256, blob.size, blob.entry_id, blob.revision) != (
            checksum,
            size,
            entry_id,
            revision,
        ):
            raise ApiError(409, "blob_mismatch", "This file ID has different content or metadata.")
    else:
        used = db.scalar(
            select(func.coalesce(func.sum(BlobObject.size), 0)).where(
                BlobObject.user_id == user_id,
            )
        )
        if used + size > limits.storage_limit_bytes:
            raise ApiError(409, "storage_limit", "The storage limit has been reached.")
        blob = BlobObject(
            user_id=user_id,
            id=blob_id,
            entry_id=entry_id,
            revision=revision,
            storage_key=f"{user_id.hex}/{uuid.uuid4().hex}",
            size=size,
            sha256=checksum,
            status="pending",
        )
        db.add(blob)
    # Reserve the object durably before touching external storage. A failed commit or
    # interrupted object write remains discoverable by the cleanup worker.
    db.commit()
    upload_identity(request, db)
    blob = db.get(BlobObject, (user_id, blob_id), populate_existing=True)
    if blob is None:
        raise ApiError(409, "upload_expired", "The upload expired. Retry the upload.")
    if blob.status == "pending":
        try:
            storage_for(request).put(blob.storage_key, source, checksum)
        except OSError:
            raise ApiError(
                503, "storage_unavailable", "File storage is temporarily unavailable."
            ) from None
        blob.status = "ready"
    receipt = BlobReceipt(
        blob_id=blob.id,
        entry_id=blob.entry_id,
        revision=blob.revision,
        size=blob.size,
        sha256=blob.sha256,
    )
    db.commit()
    return receipt


def download_blob(request: Request, db: Session, blob_id: uuid.UUID) -> bytes:
    user, _session, _token = principal(request, db)
    blob = db.get(BlobObject, (user.id, blob_id))
    if blob is None or blob.status != "linked":
        raise ApiError(404, "blob_not_found", "The encrypted file was not found.")
    try:
        data = storage_for(request).get(blob.storage_key, blob.size)
    except OSError:
        raise ApiError(
            503, "storage_unavailable", "File storage is temporarily unavailable."
        ) from None
    if len(data) != blob.size or hashlib.sha256(data).hexdigest() != blob.sha256:
        raise ApiError(503, "storage_integrity", "The encrypted file could not be verified.")
    return data
