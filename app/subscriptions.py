from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import UserSubscription

PlanType = Literal["free", "pro"]
SubscriptionStatus = Literal["active", "trialing", "past_due", "canceled", "expired"]


@dataclass(frozen=True)
class UserLimits:
    plan: PlanType
    configured_plan: PlanType
    status: SubscriptionStatus
    storage_limit_bytes: int
    entry_limit: int
    blob_max_bytes: int
    storage_quota_override_bytes: int | None
    max_entries_override: int | None
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    provider: str
    note: str | None
    updated_at: datetime | None


def evaluate_subscription(
    sub: UserSubscription | None,
    settings: Settings,
    now: datetime | None = None,
) -> UserLimits:
    now = now or datetime.now(UTC)
    if sub is None:
        return UserLimits(
            plan="free",
            configured_plan="free",
            status="active",
            storage_limit_bytes=settings.storage_quota_bytes,
            entry_limit=settings.max_entries,
            blob_max_bytes=settings.blob_max_bytes,
            storage_quota_override_bytes=None,
            max_entries_override=None,
            current_period_start=None,
            current_period_end=None,
            cancel_at_period_end=False,
            provider="manual",
            note=None,
            updated_at=None,
        )

    configured_plan: PlanType = "pro" if sub.plan == "pro" else "free"
    period_expired = sub.current_period_end is not None and sub.current_period_end <= now
    if sub.status == "expired" or period_expired:
        effective_status: SubscriptionStatus = "expired"
        entitled_active = False
    elif sub.status == "canceled":
        effective_status = "canceled"
        entitled_active = sub.current_period_end is not None and sub.current_period_end > now
    elif sub.status in ("active", "trialing", "past_due"):
        effective_status = sub.status  # type: ignore[assignment]
        entitled_active = True
    else:
        effective_status = "expired"
        entitled_active = False

    effective_plan: PlanType = "pro" if (configured_plan == "pro" and entitled_active) else "free"
    if effective_plan == "pro":
        base_storage = max(settings.storage_quota_bytes, settings.pro_storage_quota_bytes)
        base_entries = max(settings.max_entries, settings.pro_max_entries)
        blob_max = max(settings.blob_max_bytes, settings.pro_blob_max_bytes)
    else:
        base_storage = settings.storage_quota_bytes
        base_entries = settings.max_entries
        blob_max = settings.blob_max_bytes

    storage_limit = (
        sub.storage_quota_override_bytes
        if entitled_active and sub.storage_quota_override_bytes is not None
        else base_storage
    )
    entry_limit = (
        sub.max_entries_override
        if entitled_active and sub.max_entries_override is not None
        else base_entries
    )
    return UserLimits(
        plan=effective_plan,
        configured_plan=configured_plan,
        status=effective_status,
        storage_limit_bytes=storage_limit,
        entry_limit=entry_limit,
        blob_max_bytes=blob_max,
        storage_quota_override_bytes=sub.storage_quota_override_bytes,
        max_entries_override=sub.max_entries_override,
        current_period_start=sub.current_period_start,
        current_period_end=sub.current_period_end,
        cancel_at_period_end=sub.cancel_at_period_end,
        provider=sub.provider,
        note=sub.note,
        updated_at=sub.updated_at,
    )


def effective_limits(
    db: Session,
    settings: Settings,
    user_id: UUID,
    now: datetime | None = None,
) -> UserLimits:
    sub = db.get(UserSubscription, user_id)
    return evaluate_subscription(sub, settings, now)
