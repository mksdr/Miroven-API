from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse
from pydantic import EmailStr, Field, model_validator
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.account import DeletionStatus
from app.auth import check_csrf, principal
from app.config import Settings
from app.errors import ApiError
from app.models import (
    AccountDeletion,
    AuthSession,
    BlobObject,
    Entry,
    Job,
    JournalChange,
    RateWindow,
    SyncSnapshot,
    User,
    UserSubscription,
    Vault,
)
from app.profile import PROFILE_DOCUMENT_ID
from app.routes import ERRORS, Database
from app.schemas import Contract
from app.subscriptions import PlanType, SubscriptionStatus, evaluate_subscription

router = APIRouter(responses=ERRORS)
ADMIN_UI_PATH = Path(__file__).with_name("admin_ui.html")
ADMIN_CSP = (
    "default-src 'none'; "
    "script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "frame-ancestors 'none'"
)


class DailyCount(Contract):
    date: str
    count: int


class UserStats(Contract):
    total_users: int
    active_users: int
    deleting_users: int
    vault_registered_users: int
    vault_registration_rate: float
    active_sessions: int
    active_session_users: int
    joined_24h: int
    joined_7d: int
    joined_30d: int
    daily_signups: list[DailyCount]


class SubscriptionStatusCounts(Contract):
    active: int
    trialing: int
    past_due: int
    canceled: int
    expired: int


class SubscriptionStats(Contract):
    free_users: int
    pro_users: int
    pro_conversion_rate: float
    by_status: SubscriptionStatusCounts
    expiring_7d: int
    cancel_scheduled: int


class UsageBuckets(Contract):
    unused: int
    low: int
    medium: int
    high: int


class StorageStats(Contract):
    total_blob_bytes: int
    total_blobs: int
    linked_blobs: int
    ready_blobs: int
    pending_blobs: int
    total_entries: int
    active_entries: int
    deleted_entries: int
    profile_documents: int
    journal_changes: int
    active_snapshots: int
    usage_distribution: UsageBuckets


class FailedDeletionItem(Contract):
    deletion_id: UUID
    user_id: UUID | None
    attempts: int
    available_at: datetime
    expires_at: datetime


class OperationStats(Contract):
    jobs_pending: int
    jobs_failed: int
    jobs_complete: int
    oldest_pending_job_at: datetime | None
    deletions_pending: int
    deletions_failed: int
    deletions_complete: int
    failed_deletions: list[FailedDeletionItem]
    active_rate_windows: int
    max_rate_window_count: int


class AdminStatsResponse(Contract):
    generated_at: datetime
    admin_email: EmailStr
    users: UserStats
    subscriptions: SubscriptionStats
    storage: StorageStats
    operations: OperationStats


class AdminUserSummary(Contract):
    user_id: UUID
    email: EmailStr
    created_at: datetime
    deleting_at: datetime | None
    has_vault: bool
    active_sessions: int
    storage_bytes: int
    storage_limit_bytes: int
    entries: int
    entry_limit: int
    blob_max_bytes: int
    plan: PlanType
    configured_plan: PlanType
    subscription_status: SubscriptionStatus
    storage_quota_override_bytes: int | None
    max_entries_override: int | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    provider: str
    note: str | None
    subscription_updated_at: datetime | None


class AdminUserPage(Contract):
    total: int
    items: list[AdminUserSummary]


class SubscriptionUpdate(Contract):
    plan: PlanType
    status: SubscriptionStatus = "active"
    storage_quota_override_bytes: Annotated[int | None, Field(ge=1, le=1099511627776)] = None
    max_entries_override: Annotated[int | None, Field(ge=1, le=10000000)] = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    provider: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")] = (
        "manual"
    )
    note: Annotated[str | None, Field(max_length=500)] = None

    @model_validator(mode="after")
    def validate_period(self):
        if (
            self.current_period_start is not None
            and self.current_period_end is not None
            and self.current_period_end <= self.current_period_start
        ):
            raise ValueError("current_period_end must be after current_period_start")
        if self.note is not None:
            self.note = self.note.strip() or None
        return self


def require_admin(request: Request, db: Session, mutate: bool = False) -> User:
    settings: Settings = request.app.state.settings
    user, _session, token = principal(request, db)
    if mutate:
        check_csrf(request, token)
    if not settings.admin_email_set or user.email.lower() not in settings.admin_email_set:
        raise ApiError(403, "admin_required", "Administrator access is required.")
    return user


def _pro_sql_condition(now: datetime):
    return and_(
        UserSubscription.plan == "pro",
        UserSubscription.status != "expired",
        or_(
            UserSubscription.current_period_end.is_(None),
            UserSubscription.current_period_end > now,
        ),
        or_(
            UserSubscription.status.in_(("active", "trialing", "past_due")),
            and_(
                UserSubscription.status == "canceled",
                UserSubscription.current_period_end.is_not(None),
            ),
        ),
    )


def _build_user_summary(
    db: Session,
    settings: Settings,
    user: User,
    now: datetime,
) -> AdminUserSummary:
    sub = db.get(UserSubscription, user.id)
    limits = evaluate_subscription(sub, settings, now)
    has_vault = db.get(Vault, user.id) is not None
    active_sessions = (
        db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        )
        or 0
    )
    storage_bytes = (
        db.scalar(
            select(func.coalesce(func.sum(BlobObject.size), 0)).where(BlobObject.user_id == user.id)
        )
        or 0
    )
    entries = (
        db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.user_id == user.id, Entry.id != PROFILE_DOCUMENT_ID)
        )
        or 0
    )
    return AdminUserSummary(
        user_id=user.id,
        email=user.email,
        created_at=user.created_at,
        deleting_at=user.deleting_at,
        has_vault=has_vault,
        active_sessions=active_sessions,
        storage_bytes=storage_bytes,
        storage_limit_bytes=limits.storage_limit_bytes,
        entries=entries,
        entry_limit=limits.entry_limit,
        blob_max_bytes=limits.blob_max_bytes,
        plan=limits.plan,
        configured_plan=limits.configured_plan,
        subscription_status=limits.status,
        storage_quota_override_bytes=limits.storage_quota_override_bytes,
        max_entries_override=limits.max_entries_override,
        current_period_start=limits.current_period_start,
        current_period_end=limits.current_period_end,
        cancel_at_period_end=limits.cancel_at_period_end,
        provider=limits.provider,
        note=limits.note,
        subscription_updated_at=limits.updated_at,
    )


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page(request: Request):
    settings: Settings = request.app.state.settings
    if not settings.admin_email_set:
        raise ApiError(404, "admin_disabled", "The administrator page is not enabled.")
    html = ADMIN_UI_PATH.read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": ADMIN_CSP,
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/api/v1/admin/stats", response_model=AdminStatsResponse, tags=["admin"])
def admin_stats(request: Request, db: Database):
    admin_user = require_admin(request, db)
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)

    total_users = db.scalar(select(func.count()).select_from(User)) or 0
    deleting_users = (
        db.scalar(select(func.count()).select_from(User).where(User.deleting_at.is_not(None))) or 0
    )
    active_users = max(0, total_users - deleting_users)
    vault_registered_users = (
        db.scalar(
            select(func.count())
            .select_from(Vault)
            .join(User, User.id == Vault.user_id)
            .where(User.deleting_at.is_(None))
        )
        or 0
    )
    vault_rate = round((vault_registered_users / active_users) * 100, 1) if active_users else 0.0
    active_sessions = (
        db.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
        )
        or 0
    )
    active_session_users = (
        db.scalar(
            select(func.count(func.distinct(AuthSession.user_id))).where(
                AuthSession.revoked_at.is_(None), AuthSession.expires_at > now
            )
        )
        or 0
    )
    joined_24h = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.deleting_at.is_(None), User.created_at >= now - timedelta(hours=24))
        )
        or 0
    )
    joined_7d = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.deleting_at.is_(None), User.created_at >= now - timedelta(days=7))
        )
        or 0
    )
    joined_30d = (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.deleting_at.is_(None), User.created_at >= now - timedelta(days=30))
        )
        or 0
    )

    today_utc = now.date()
    start_date = today_utc - timedelta(days=13)
    start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
    recent_dates = db.scalars(
        select(User.created_at).where(User.deleting_at.is_(None), User.created_at >= start_dt)
    ).all()
    counts_by_day: dict[str, int] = {
        (start_date + timedelta(days=offset)).isoformat(): 0 for offset in range(14)
    }
    for dt in recent_dates:
        day_key = dt.astimezone(UTC).date().isoformat()
        if day_key in counts_by_day:
            counts_by_day[day_key] += 1
    daily_signups = [DailyCount(date=day, count=count) for day, count in counts_by_day.items()]

    subs = list(
        db.scalars(
            select(UserSubscription)
            .join(User, User.id == UserSubscription.user_id)
            .where(User.deleting_at.is_(None))
        )
    )
    sub_by_user = {sub.user_id: sub for sub in subs}
    pro_users = 0
    expiring_7d = 0
    cancel_scheduled = 0
    status_counts = {"active": 0, "trialing": 0, "past_due": 0, "canceled": 0, "expired": 0}
    for sub in subs:
        limits = evaluate_subscription(sub, settings, now)
        status_counts[limits.status] += 1
        if limits.plan == "pro":
            pro_users += 1
            if sub.cancel_at_period_end:
                cancel_scheduled += 1
            if (
                sub.current_period_end is not None
                and now < sub.current_period_end <= now + timedelta(days=7)
            ):
                expiring_7d += 1
    unconfigured_users = max(0, active_users - len(subs))
    status_counts["active"] += unconfigured_users
    free_users = max(0, active_users - pro_users)
    pro_rate = round((pro_users / active_users) * 100, 1) if active_users else 0.0

    blob_rows = db.execute(
        select(
            BlobObject.status,
            func.count(),
            func.coalesce(func.sum(BlobObject.size), 0),
        ).group_by(BlobObject.status)
    ).all()
    blob_counts = {"linked": 0, "ready": 0, "pending": 0}
    total_blob_bytes = 0
    for status_name, count_val, size_val in blob_rows:
        if status_name in blob_counts:
            blob_counts[status_name] = int(count_val)
        total_blob_bytes += int(size_val)
    total_blobs = sum(blob_counts.values())

    active_entries = (
        db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.id != PROFILE_DOCUMENT_ID, Entry.deleted.is_(False))
        )
        or 0
    )
    deleted_entries = (
        db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.id != PROFILE_DOCUMENT_ID, Entry.deleted.is_(True))
        )
        or 0
    )
    profile_documents = (
        db.scalar(
            select(func.count())
            .select_from(Entry)
            .where(Entry.id == PROFILE_DOCUMENT_ID, Entry.deleted.is_(False))
        )
        or 0
    )
    journal_changes = db.scalar(select(func.count()).select_from(JournalChange)) or 0
    active_snapshots = (
        db.scalar(
            select(func.count()).select_from(SyncSnapshot).where(SyncSnapshot.expires_at > now)
        )
        or 0
    )

    user_usage_rows = db.execute(
        select(User.id, func.coalesce(func.sum(BlobObject.size), 0))
        .outerjoin(BlobObject, BlobObject.user_id == User.id)
        .where(User.deleting_at.is_(None))
        .group_by(User.id)
    ).all()
    buckets = {"unused": 0, "low": 0, "medium": 0, "high": 0}
    for uid, used_bytes in user_usage_rows:
        used = int(used_bytes)
        if used == 0:
            buckets["unused"] += 1
            continue
        user_limits = evaluate_subscription(sub_by_user.get(uid), settings, now)
        ratio = used / max(1, user_limits.storage_limit_bytes)
        if ratio >= 0.8:
            buckets["high"] += 1
        elif ratio >= 0.5:
            buckets["medium"] += 1
        else:
            buckets["low"] += 1

    job_rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    job_counts = {"pending": 0, "failed": 0, "complete": 0}
    for status_name, count_val in job_rows:
        if status_name in job_counts:
            job_counts[status_name] = int(count_val)
    oldest_pending_job_at = db.scalar(
        select(func.min(Job.available_at)).where(Job.status == "pending")
    )

    del_rows = db.execute(
        select(AccountDeletion.status, func.count()).group_by(AccountDeletion.status)
    ).all()
    del_counts = {"pending": 0, "failed": 0, "complete": 0}
    for status_name, count_val in del_rows:
        if status_name in del_counts:
            del_counts[status_name] = int(count_val)
    failed_del_rows = list(
        db.scalars(
            select(AccountDeletion)
            .where(AccountDeletion.status == "failed")
            .order_by(AccountDeletion.available_at.asc())
            .limit(20)
        )
    )
    failed_deletions = [
        FailedDeletionItem(
            deletion_id=item.id,
            user_id=item.user_id,
            attempts=item.attempts,
            available_at=item.available_at,
            expires_at=item.expires_at,
        )
        for item in failed_del_rows
    ]

    window_start = now.replace(minute=0, second=0, microsecond=0)
    active_rate_windows = (
        db.scalar(
            select(func.count())
            .select_from(RateWindow)
            .where(RateWindow.window_start >= window_start)
        )
        or 0
    )
    max_rate_window_count = (
        db.scalar(
            select(func.coalesce(func.max(RateWindow.count), 0)).where(
                RateWindow.window_start >= window_start
            )
        )
        or 0
    )

    return AdminStatsResponse(
        generated_at=now,
        admin_email=admin_user.email,
        users=UserStats(
            total_users=total_users,
            active_users=active_users,
            deleting_users=deleting_users,
            vault_registered_users=vault_registered_users,
            vault_registration_rate=vault_rate,
            active_sessions=active_sessions,
            active_session_users=active_session_users,
            joined_24h=joined_24h,
            joined_7d=joined_7d,
            joined_30d=joined_30d,
            daily_signups=daily_signups,
        ),
        subscriptions=SubscriptionStats(
            free_users=free_users,
            pro_users=pro_users,
            pro_conversion_rate=pro_rate,
            by_status=SubscriptionStatusCounts(**status_counts),
            expiring_7d=expiring_7d,
            cancel_scheduled=cancel_scheduled,
        ),
        storage=StorageStats(
            total_blob_bytes=total_blob_bytes,
            total_blobs=total_blobs,
            linked_blobs=blob_counts["linked"],
            ready_blobs=blob_counts["ready"],
            pending_blobs=blob_counts["pending"],
            total_entries=active_entries + deleted_entries,
            active_entries=active_entries,
            deleted_entries=deleted_entries,
            profile_documents=profile_documents,
            journal_changes=journal_changes,
            active_snapshots=active_snapshots,
            usage_distribution=UsageBuckets(**buckets),
        ),
        operations=OperationStats(
            jobs_pending=job_counts["pending"],
            jobs_failed=job_counts["failed"],
            jobs_complete=job_counts["complete"],
            oldest_pending_job_at=oldest_pending_job_at,
            deletions_pending=del_counts["pending"],
            deletions_failed=del_counts["failed"],
            deletions_complete=del_counts["complete"],
            failed_deletions=failed_deletions,
            active_rate_windows=active_rate_windows,
            max_rate_window_count=max_rate_window_count,
        ),
    )


@router.get("/api/v1/admin/users", response_model=AdminUserPage, tags=["admin"])
def list_users(
    request: Request,
    db: Database,
    q: Annotated[str | None, Query(max_length=320)] = None,
    plan: Literal["all", "free", "pro"] = "all",
    sort: Literal["created_desc", "created_asc", "storage_desc", "entries_desc"] = "created_desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
):
    require_admin(request, db)
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)

    storage_subq = (
        select(
            BlobObject.user_id.label("user_id"),
            func.coalesce(func.sum(BlobObject.size), 0).label("storage_bytes"),
        )
        .group_by(BlobObject.user_id)
        .subquery()
    )
    entries_subq = (
        select(
            Entry.user_id.label("user_id"),
            func.count().label("entry_count"),
        )
        .where(Entry.id != PROFILE_DOCUMENT_ID)
        .group_by(Entry.user_id)
        .subquery()
    )

    filters = []
    if q and q.strip():
        term = q.strip().lower()
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        try:
            parsed_uuid = UUID(term)
            filters.append(
                or_(User.id == parsed_uuid, User.email.ilike(f"%{escaped}%", escape="\\"))
            )
        except ValueError:
            filters.append(User.email.ilike(f"%{escaped}%", escape="\\"))

    pro_cond = _pro_sql_condition(now)
    if plan == "pro":
        filters.append(pro_cond)
    elif plan == "free":
        filters.append(or_(UserSubscription.user_id.is_(None), ~pro_cond))

    count_stmt = (
        select(func.count())
        .select_from(User)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = db.scalar(count_stmt) or 0

    stmt = (
        select(User)
        .outerjoin(UserSubscription, UserSubscription.user_id == User.id)
        .outerjoin(storage_subq, storage_subq.c.user_id == User.id)
        .outerjoin(entries_subq, entries_subq.c.user_id == User.id)
    )
    if filters:
        stmt = stmt.where(*filters)

    storage_col = func.coalesce(storage_subq.c.storage_bytes, 0)
    entries_col = func.coalesce(entries_subq.c.entry_count, 0)
    if sort == "created_asc":
        stmt = stmt.order_by(User.created_at.asc(), User.id.asc())
    elif sort == "storage_desc":
        stmt = stmt.order_by(storage_col.desc(), User.created_at.desc())
    elif sort == "entries_desc":
        stmt = stmt.order_by(entries_col.desc(), User.created_at.desc())
    else:
        stmt = stmt.order_by(
            case((User.deleting_at.is_(None), 0), else_=1).asc(),
            User.created_at.desc(),
            User.id.asc(),
        )

    users = list(db.scalars(stmt.limit(limit).offset(offset)))
    items = [_build_user_summary(db, settings, item, now) for item in users]
    return AdminUserPage(total=total, items=items)


@router.get("/api/v1/admin/users/{user_id}", response_model=AdminUserSummary, tags=["admin"])
def get_user_detail(
    user_id: Annotated[UUID, PathParam()],
    request: Request,
    db: Database,
):
    require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "user_not_found", "The requested user was not found.")
    return _build_user_summary(db, request.app.state.settings, user, datetime.now(UTC))


@router.put(
    "/api/v1/admin/users/{user_id}/subscription",
    response_model=AdminUserSummary,
    tags=["admin"],
)
def update_user_subscription(
    user_id: Annotated[UUID, PathParam()],
    body: SubscriptionUpdate,
    request: Request,
    db: Database,
):
    require_admin(request, db, mutate=True)
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise ApiError(404, "user_not_found", "The requested user was not found.")
    if target.deleting_at is not None:
        raise ApiError(409, "account_deleting", "Cannot modify a deleting account.")
    now = datetime.now(UTC)
    sub = db.get(UserSubscription, user_id)
    if sub is None:
        sub = UserSubscription(user_id=user_id)
        db.add(sub)
    sub.plan = body.plan
    sub.status = body.status
    sub.storage_quota_override_bytes = body.storage_quota_override_bytes
    sub.max_entries_override = body.max_entries_override
    sub.current_period_start = body.current_period_start
    sub.current_period_end = body.current_period_end
    sub.cancel_at_period_end = body.cancel_at_period_end
    sub.provider = body.provider
    sub.note = body.note
    sub.updated_at = now
    db.flush()
    summary = _build_user_summary(db, request.app.state.settings, target, now)
    db.commit()
    return summary


@router.post(
    "/api/v1/admin/deletions/{deletion_id}/retry",
    response_model=DeletionStatus,
    tags=["admin"],
)
def admin_retry_deletion(
    deletion_id: Annotated[UUID, PathParam()],
    request: Request,
    db: Database,
):
    require_admin(request, db, mutate=True)
    task = db.get(AccountDeletion, deletion_id, with_for_update=True)
    if task is None:
        raise ApiError(404, "deletion_not_found", "The deletion task was not found.")
    if task.status != "failed":
        raise ApiError(409, "deletion_not_failed", "Only failed deletion tasks can be retried.")
    task.status = "pending"
    task.attempts = 0
    task.available_at = datetime.now(UTC)
    db.commit()
    return DeletionStatus(deletion_id=task.id, status=task.status)
