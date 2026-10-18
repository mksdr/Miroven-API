import hashlib
import tempfile
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app import sync
from app.auth import check_csrf, principal
from app.blobs import authorize_upload, download_blob, save_blob
from app.errors import ApiError
from app.profile import PROFILE_DOCUMENT_ID
from app.routes import ERRORS, Database
from app.sync_schemas import BlobReceipt, ChangePage, PushAccepted, SnapshotPage, SyncWrite

router = APIRouter(responses=ERRORS)
Cursor = Annotated[str | None, Query(min_length=1, max_length=1024)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.post("/api/v1/sync/push", response_model=PushAccepted, tags=["sync"])
def push(body: SyncWrite, request: Request, db: Database):
    """Commit one encrypted journal or singleton profile operation.

    The JSON body is limited to 128 KiB. Profile images are inside the encrypted
    payload, never blobs. Revision conflicts and idempotent retries apply to both
    document kinds. The server cannot validate plaintext profile fields or pixels.
    """
    user, _session, token = principal(request, db)
    check_csrf(request, token)
    result = sync.push(db, request.app.state.settings, user.id, body)
    db.commit()
    return result


@router.get("/api/v1/sync/changes", response_model=ChangePage, tags=["sync"])
def changes(request: Request, db: Database, cursor: Cursor = None, limit: PageSize = 50):
    """Page encrypted journal and profile changes, including tombstones, in one cursor stream."""
    user, _session, _token = principal(request, db)
    result = sync.changes(db, request.app.state.settings, user.id, cursor, limit)
    db.commit()
    return result


@router.get("/api/v1/sync/snapshot", response_model=SnapshotPage, tags=["sync"])
def snapshot(request: Request, db: Database, cursor: Cursor = None, limit: PageSize = 50):
    """Read a frozen snapshot including the encrypted singleton profile or its tombstone."""
    user, _session, _token = principal(request, db)
    result = sync.snapshot(db, request.app.state.settings, user.id, cursor, limit)
    db.commit()
    return result


@router.post(
    "/api/v1/blobs",
    response_model=BlobReceipt,
    tags=["files"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def upload_blob(
    request: Request,
    db: Database,
    blob_id: UUID,
    entry_id: Annotated[
        UUID,
        Query(description=f"Journal ID; profile ID {PROFILE_DOCUMENT_ID} cannot receive blobs."),
    ],
    revision: Annotated[int, Query(ge=1, le=2147483646)],
):
    if request.headers.get("content-type", "").lower() != "application/octet-stream":
        raise ApiError(
            415, "invalid_media_type", "Send the encrypted file as application/octet-stream."
        )
    await run_in_threadpool(authorize_upload, request, db, entry_id)
    checksum = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=65536, mode="w+b") as source:
        try:
            with anyio.fail_after(30):
                async for chunk in request.stream():
                    size += len(chunk)
                    checksum.update(chunk)
                    await run_in_threadpool(source.write, chunk)
        except TimeoutError:
            raise ApiError(408, "upload_timeout", "The file upload took too long.") from None
        if size == 0:
            raise ApiError(422, "empty_file", "The encrypted file is empty.")
        return await run_in_threadpool(
            save_blob,
            request,
            db,
            blob_id,
            entry_id,
            revision,
            source,
            size,
            checksum.hexdigest(),
        )


@router.get(
    "/api/v1/blobs/{blob_id}",
    response_class=Response,
    tags=["files"],
    responses={
        200: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            }
        },
    },
)
def get_blob(blob_id: UUID, request: Request, db: Database):
    return Response(download_blob(request, db, blob_id), media_type="application/octet-stream")
