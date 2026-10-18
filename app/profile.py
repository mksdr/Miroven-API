"""Public identity of the account-scoped, opaque encrypted profile document."""

from uuid import UUID

from app.errors import ApiError

PROFILE_DOCUMENT_ID = UUID("00000000-0000-4000-8000-000000000001")
PROFILE_DOCUMENT_DESCRIPTION = (
    "Encrypted document ID, scoped to the authenticated account. "
    f"{PROFILE_DOCUMENT_ID} is reserved for the singleton profile. "
    "The profile uses the same revisions, cursors and encryption as journal entries, "
    "but cannot reference blobs and does not consume the journal entry limit. "
    "Its image is included inside the encrypted payload."
)


def require_journal_blob(entry_id: UUID) -> None:
    if entry_id == PROFILE_DOCUMENT_ID:
        raise ApiError(
            422,
            "profile_attachment_forbidden",
            "Profile images belong inside the encrypted profile payload, not in blobs.",
        )
