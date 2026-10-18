"""Account deletion serializes with login and every authenticated write."""

import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from pydantic import Field
from sqlalchemy import String, cast, delete, func, select, update
from sqlalchemy.orm import Session

from app.auth import check_csrf, lock_email, principal, rate_hit
from app.errors import ApiError
from app.models import AccountDeletion, AuthSession, BlobObject, Entry, Job, LoginChallenge, User
from app.profile import PROFILE_DOCUMENT_ID
from app.routes import ERRORS, Database
from app.schemas import Contract
from app.security import digest, new_token
from app.subscriptions import PlanType, SubscriptionStatus, effective_limits

router = APIRouter(responses=ERRORS)


class DeleteAccount(Contract):
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)


class DeletionStatus(Contract):
    deletion_id: UUID
    status: Literal["pending", "complete", "failed"]


class DeletionAccepted(DeletionStatus):
    deletion_token: str
    expires_at: datetime


class Usage(Contract):
    storage_bytes: int = Field(
        description=(
            "Reserved and stored blob bytes, including unlinked uploads. "
            "Excludes encrypted document payloads, including the profile."
        )
    )
    storage_limit_bytes: int
    entries: int = Field(
        description="Journal entries including tombstones; excludes the singleton profile document."
    )
    entry_limit: int = Field(
        description="Journal entry limit including tombstones; excludes the profile."
    )
    blob_max_bytes: int
    plan: PlanType = "free"
    subscription_status: SubscriptionStatus = "active"
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False


@router.get("/api/v1/account/usage", response_model=Usage, tags=["account"])
def usage(request: Request, db: Database):
    user, _, _ = principal(request, db)
    settings = request.app.state.settings
    limits = effective_limits(db, settings, user.id)
    return Usage(
        storage_bytes=db.scalar(
            select(func.coalesce(func.sum(BlobObject.size), 0)).where(BlobObject.user_id == user.id)
        ),
        storage_limit_bytes=limits.storage_limit_bytes,
        entries=db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.user_id == user.id, Entry.id != PROFILE_DOCUMENT_ID)
        ),
        entry_limit=limits.entry_limit,
        blob_max_bytes=limits.blob_max_bytes,
        plan=limits.plan,
        subscription_status=limits.status,
        current_period_end=limits.current_period_end,
        cancel_at_period_end=limits.cancel_at_period_end,
    )


@router.delete(
    "/api/v1/account", response_model=DeletionAccepted, status_code=202, tags=["account"]
)
def delete_account(body: DeleteAccount, request: Request, response: Response, db: Database):
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name, "")
    # Same lock order as verify_code: email -> challenge/user. Never user -> email.
    email = db.scalar(
        select(User.email)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.token_digest == digest(settings, "session", token))
    )
    if email is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    lock_email(db, settings, email)
    user, _, token = principal(request, db)
    check_csrf(request, token)
    now = datetime.now(UTC)
    allowed = rate_hit(db, settings, "delete:" + str(user.id), settings.code_attempt_limit, now)
    challenge = db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.email == email, LoginChallenge.consumed_at.is_(None))
        .order_by(LoginChallenge.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    valid = False
    if allowed and challenge and challenge.expires_at > now:
        if challenge.attempts < settings.code_attempt_limit:
            challenge.attempts += 1
            valid = hmac.compare_digest(
                challenge.code_digest, digest(settings, "login-code", email + ":" + body.code)
            )
    if not valid:
        db.commit()  # Failed reauthentication attempts must remain counted.
        raise ApiError(401, "invalid_code", "The sign-in code is invalid or expired.")
    challenge.consumed_at = now
    user.deleting_at = now
    db.execute(update(AuthSession).where(AuthSession.user_id == user.id).values(revoked_at=now))
    receipt_token = new_token()
    task = AccountDeletion(
        id=user.id,  # Pseudonymous deletion ledger also identifies users in older backups.
        user_id=user.id,
        token_digest=digest(settings, "deletion", receipt_token),
        available_at=now,
        expires_at=now + timedelta(days=30),
    )
    db.add(task)
    db.flush()
    result = DeletionAccepted(
        deletion_id=task.id,
        status=task.status,
        deletion_token=receipt_token,
        expires_at=task.expires_at,
    )
    db.commit()
    response.delete_cookie(
        settings.cookie_name,
        path="/",
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
    )
    return result


@router.get("/api/v1/account/deletion", response_model=DeletionStatus, tags=["account"])
def deletion_status(
    request: Request,
    db: Database,
    authorization: Annotated[str, Header(description="Bearer deletion receipt token")] = "",
):
    token = authorization.removeprefix("Bearer ")
    task = None
    if authorization.startswith("Bearer ") and 1 <= len(token) <= 100:
        task = db.scalar(
            select(AccountDeletion).where(
                AccountDeletion.token_digest
                == digest(request.app.state.settings, "deletion", token),
                AccountDeletion.expires_at > datetime.now(UTC),
            )
        )
    if task is None:
        raise ApiError(401, "invalid_deletion_token", "The deletion receipt is invalid or expired.")
    return DeletionStatus(deletion_id=task.id, status=task.status)


def process_deletion(engine, settings, storage) -> bool:
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        task = db.scalar(
            select(AccountDeletion)
            .where(
                AccountDeletion.status == "pending",
                AccountDeletion.available_at <= now,
            )
            .order_by(AccountDeletion.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return False
        email = db.scalar(select(User.email).where(User.id == task.user_id))
        if email is None:
            # A missing user would lose the manifest; require operator investigation.
            task.status = "failed"
            return True
        lock_email(db, settings, email)
        user = db.scalar(select(User).where(User.id == task.user_id).with_for_update())
        if user.deleting_at is None:
            task.status = "failed"
            return True
        blobs = list(
            db.scalars(
                select(BlobObject)
                .where(
                    BlobObject.user_id == user.id,
                )
                .limit(100)
            )
        )
        try:
            for blob in blobs:
                storage.delete(blob.storage_key)
        except OSError:
            task.attempts += 1
            task.available_at = now + timedelta(seconds=min(3600, 5 * 2 ** min(task.attempts, 10)))
            if task.attempts >= 10:
                task.status = "failed"
            return True
        task.attempts = 0
        task.available_at = now
        if len(blobs) == 100:
            # The object manifest must persist across process crashes. Remove references
            # first; this account is permanently blocked and never serves sync again.
            from app.models import Entry, JournalChange, SnapshotItem

            for model in (Entry, JournalChange, SnapshotItem):
                db.execute(delete(model).where(model.user_id == user.id))
            db.execute(
                delete(BlobObject).where(
                    BlobObject.user_id == user.id, BlobObject.id.in_([b.id for b in blobs])
                )
            )
            return True
        db.execute(
            delete(Job).where(
                Job.kind == "login_email",
                Job.deduplication_key.in_(
                    select(cast(LoginChallenge.id, String)).where(LoginChallenge.email == email)
                ),
            )
        )
        db.execute(delete(LoginChallenge).where(LoginChallenge.email == email))
        db.delete(user)
        task.user_id = None
        task.status = "complete"
        task.completed_at = now
        task.attempts = 0
    return True


def retry_deletion(engine, task_id):
    with Session(engine) as db, db.begin():
        task = db.get(AccountDeletion, task_id, with_for_update=True)
        if task is None or task.status != "failed":
            raise ValueError("Deletion task must exist and be failed")
        task.status = "pending"
        task.attempts = 0
        task.available_at = datetime.now(UTC)
