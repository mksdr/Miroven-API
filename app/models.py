import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginChallenge(Base):
    __tablename__ = "login_challenges"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    code_digest: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_login_challenges_email_created", "email", "created_at"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Vault(Base):
    __tablename__ = "vaults"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer)
    wrapped_vault_key: Mapped[bytes] = mapped_column(LargeBinary)
    wrapping_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    recovery_salt: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("octet_length(wrapped_vault_key) = 48", name="key_length"),
        CheckConstraint("octet_length(wrapping_nonce) = 12", name="nonce_length"),
        CheckConstraint("octet_length(recovery_salt) = 16", name="salt_length"),
    )


class UserSubscription(Base):
    __tablename__ = "subscriptions"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[str] = mapped_column(String(16), default="free")
    status: Mapped[str] = mapped_column(String(16), default="active")
    storage_quota_override_bytes: Mapped[int | None] = mapped_column(BigInteger)
    max_entries_override: Mapped[int | None] = mapped_column(Integer)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32), default="manual")
    provider_subscription_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    note: Mapped[str | None] = mapped_column(String(500))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("plan IN ('free', 'pro')", name="plan_allowed"),
        CheckConstraint(
            "status IN ('active', 'trialing', 'past_due', 'canceled', 'expired')",
            name="status_allowed",
        ),
        CheckConstraint(
            "storage_quota_override_bytes IS NULL OR storage_quota_override_bytes > 0",
            name="positive_quota_override",
        ),
        CheckConstraint(
            "max_entries_override IS NULL OR max_entries_override > 0",
            name="positive_entries_override",
        ),
        Index("ix_subscriptions_plan_status", "plan", "status"),
        Index("ix_subscriptions_period_end", "current_period_end"),
    )


class RateWindow(Base):
    __tablename__ = "rate_windows"
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("count > 0", name="count_positive"),)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    deduplication_key: Mapped[str] = mapped_column(String(100))
    encrypted_payload: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("kind", "deduplication_key"),
        Index("ix_jobs_status_available", "status", "available_at"),
        CheckConstraint("status IN ('pending', 'complete', 'failed')", name="status_allowed"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    # No FK: the receipt survives removal of the user. Clear user_id at completion.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID, unique=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'complete', 'failed')", name="status_allowed"),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        Index("ix_account_deletions_status_available", "status", "available_at"),
    )


class SyncState(Base):
    __tablename__ = "sync_states"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    epoch: Mapped[uuid.UUID] = mapped_column(UUID, default=uuid.uuid4)
    head: Mapped[int] = mapped_column(BigInteger, default=0)
    floor: Mapped[int] = mapped_column(BigInteger, default=0)
    document_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    write_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    write_window_count: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    write_window_bytes: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    __table_args__ = (
        CheckConstraint("floor >= 0 AND head >= floor", name="cursor_range"),
        CheckConstraint(
            "document_bytes >= 0 AND write_window_count >= 0 AND write_window_bytes >= 0",
            name="budgets_nonnegative",
        ),
    )


class BlobObject(Base):
    __tablename__ = "blob_objects"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID)
    revision: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(65), unique=True)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("size > 0 AND revision > 0", name="positive_size_revision"),
        CheckConstraint("status IN ('pending', 'ready', 'linked')", name="status_allowed"),
        Index("ix_blob_objects_user_created", "user_id", "created_at"),
    )


def blob_reference():
    # Deferred checks allow complete user deletion in one transaction, while forbidding
    # cross-user file references and removing a file that is still referenced.
    return ForeignKeyConstraint(
        ["user_id", "blob_id"],
        ["blob_objects.user_id", "blob_objects.id"],
        deferrable=True,
        initially="DEFERRED",
    )


class Entry(Base):
    __tablename__ = "entries"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    cursor: Mapped[int] = mapped_column(BigInteger)
    deleted: Mapped[bool] = mapped_column(Boolean)
    blob_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    document: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        blob_reference(),
        Index("ix_entries_user_blob", "user_id", "blob_id"),
        CheckConstraint("revision > 0 AND cursor > 0", name="positive_revision_cursor"),
    )


class SyncOperation(Base):
    __tablename__ = "sync_operations"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSONB)


class JournalChange(Base):
    __tablename__ = "journal_changes"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    cursor: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blob_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        blob_reference(),
        Index("ix_journal_changes_user_blob", "user_id", "blob_id"),
    )


class SyncSnapshot(Base):
    __tablename__ = "sync_snapshots"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    epoch: Mapped[uuid.UUID] = mapped_column(UUID)
    cursor: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_sync_snapshots_user_expires", "user_id", "expires_at"),)


class SnapshotItem(Base):
    __tablename__ = "snapshot_items"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    cursor: Mapped[int] = mapped_column(BigInteger)
    blob_id: Mapped[uuid.UUID | None] = mapped_column(UUID)
    document: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "snapshot_id"],
            ["sync_snapshots.user_id", "sync_snapshots.id"],
            ondelete="CASCADE",
        ),
        blob_reference(),
        Index("ix_snapshot_items_user_blob", "user_id", "blob_id"),
    )


class EntryBlob(Base):
    __tablename__ = "entry_blobs"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    blob_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "entry_id"], ["entries.user_id", "entries.id"], ondelete="CASCADE"
        ),
        blob_reference(),
        Index("ix_entry_blobs_user_blob", "user_id", "blob_id"),
    )


class ChangeBlob(Base):
    __tablename__ = "change_blobs"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    cursor: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    blob_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "cursor"],
            ["journal_changes.user_id", "journal_changes.cursor"],
            ondelete="CASCADE",
        ),
        blob_reference(),
        Index("ix_change_blobs_user_blob", "user_id", "blob_id"),
    )


class SnapshotBlob(Base):
    __tablename__ = "snapshot_blobs"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    blob_id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "snapshot_id", "entry_id"],
            ["snapshot_items.user_id", "snapshot_items.snapshot_id", "snapshot_items.entry_id"],
            ondelete="CASCADE",
        ),
        blob_reference(),
        Index("ix_snapshot_blobs_user_blob", "user_id", "blob_id"),
    )
