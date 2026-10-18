import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.profile import PROFILE_DOCUMENT_DESCRIPTION, PROFILE_DOCUMENT_ID
from app.schemas import Contract

Revision = Annotated[int, Field(ge=0, le=2147483646, strict=True)]


class CipherEnvelope(Contract):
    ciphertext: str = Field(
        min_length=24,
        max_length=87384,
        description="Canonical base64 of 16–65,536 ciphertext bytes, including the GCM tag.",
    )
    content_nonce: str = Field(min_length=16, max_length=16)
    wrapped_data_key: str = Field(min_length=64, max_length=64)
    key_nonce: str = Field(min_length=16, max_length=16)
    crypto_version: Literal[1] = 1

    @field_validator("ciphertext", "content_nonce", "wrapped_data_key", "key_nonce")
    @classmethod
    def canonical_bytes(cls, value, info):
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid base64") from exc
        expected = {"content_nonce": 12, "wrapped_data_key": 48, "key_nonce": 12}
        if base64.b64encode(decoded).decode() != value:
            raise ValueError("Non-canonical base64")
        if info.field_name == "ciphertext":
            if not 16 <= len(decoded) <= 65536:
                raise ValueError("Ciphertext size is outside the allowed range")
        elif len(decoded) != expected[info.field_name]:
            raise ValueError("Incorrect byte length")
        return value


class SyncWrite(Contract):
    operation_id: UUID
    entry_id: UUID = Field(description=PROFILE_DOCUMENT_DESCRIPTION)
    base_revision: Revision
    revision: Revision
    change_type: Literal["upsert", "delete"]
    payload: CipherEnvelope | None = None
    blob_id: UUID | None = None
    blob_ids: list[UUID] = Field(default_factory=list, max_length=4)
    attachment_version: Literal[1, 2] = 1

    @model_validator(mode="after")
    def valid_operation(self):
        if self.entry_id == PROFILE_DOCUMENT_ID and (self.blob_id is not None or self.blob_ids):
            raise ValueError("The encrypted profile document cannot reference blobs")
        if len(set(self.blob_ids)) != len(self.blob_ids):
            raise ValueError("Duplicate file IDs")
        if self.blob_ids and (self.blob_id is not None or self.attachment_version != 2):
            raise ValueError("Multiple files require attachment_version=2 and no blob_id")
        if self.attachment_version == 2 and self.blob_id is not None:
            raise ValueError("Use blob_ids for attachment_version=2")
        if self.revision != self.base_revision + 1:
            raise ValueError("revision must equal base_revision + 1")
        if self.change_type == "upsert" and self.payload is None:
            raise ValueError("An upsert requires an encrypted payload")
        if self.change_type == "delete" and (
            self.payload is not None or self.blob_id is not None or self.blob_ids
        ):
            raise ValueError("A deletion cannot carry a payload or blob")
        return self


class PushAccepted(Contract):
    outcome: Literal["accepted"] = "accepted"
    revision: int
    cursor: str


class SyncChange(SyncWrite):
    cursor: str


class ChangePage(Contract):
    items: list[SyncChange]
    next_cursor: str
    has_more: bool


class SnapshotPage(Contract):
    items: list[SyncChange]
    next_cursor: str | None
    has_more: bool
    resume_cursor: str


class BlobReceipt(Contract):
    blob_id: UUID
    entry_id: UUID
    revision: int
    size: int
    sha256: str
