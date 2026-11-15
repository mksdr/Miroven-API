"""Reserve account-wide sync budgets while holding the authenticated user lock."""

import json
from datetime import UTC, datetime

from app.config import Settings
from app.errors import ApiError
from app.models import SyncState


def document_size(document: dict) -> int:
    # SyncWrite contains only JSON scalars, UUID strings and canonical base64.
    # Default JSON separators match PostgreSQL JSONB::text for these documents.
    return len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def reserve_write(
    state: SyncState, settings: Settings, document: dict, previous_document: dict | None
):
    # head advances once per new operation and never falls during retention cleanup.
    # Retain all receipts: replay lookup must happen before entering this function.
    if state.head >= settings.sync_operation_limit:
        raise ApiError(
            409,
            "sync_operation_limit",
            "The sync operation limit has been reached. Contact the administrator.",
        )
    size = document_size(document)
    replaced = document_size(previous_document) if previous_document is not None else 0
    projected = state.document_bytes + 2 * size - replaced
    if projected > settings.sync_storage_quota_bytes and projected >= state.document_bytes:
        raise ApiError(
            409,
            "sync_storage_limit",
            "The encrypted document history limit has been reached. "
            "Retry after retention cleanup or contact the administrator.",
        )
    window = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    count = state.write_window_count if state.write_window_start == window else 0
    used = state.write_window_bytes if state.write_window_start == window else 0
    if count >= settings.sync_hourly_operations or used + size > settings.sync_hourly_bytes:
        raise ApiError(
            429,
            "sync_rate_limited",
            "The sync write budget has been reached. Retry in the next UTC hour.",
        )
    # Caller commits these counters with the new entry/history/receipt; errors roll back.
    state.document_bytes = projected
    state.write_window_start = window
    state.write_window_count = count + 1
    state.write_window_bytes = used + size
