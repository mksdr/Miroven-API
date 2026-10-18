import hmac
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import AuthSession, Job, LoginChallenge, RateWindow, User
from app.security import digest, encrypt_job, new_token


def lock_email(db: Session, settings: Settings, email: str):
    value = int.from_bytes(bytes.fromhex(digest(settings, "email-lock", email))[:8], signed=True)
    db.execute(select(func.pg_advisory_xact_lock(value)))


def rate_hit(db: Session, settings: Settings, key: str, limit: int, now: datetime) -> bool:
    stmt = insert(RateWindow).values(
        key_digest=digest(settings, "rate-limit", key),
        window_start=now.replace(minute=0, second=0, microsecond=0),
        count=1,
    )
    count = db.scalar(
        stmt.on_conflict_do_update(
            index_elements=[RateWindow.key_digest, RateWindow.window_start],
            set_={"count": RateWindow.count + 1},
        ).returning(RateWindow.count)
    )
    return count <= limit


def request_code(db: Session, settings: Settings, email: str, ip: str):
    now = datetime.now(UTC)
    # IP budget is checked even if the email budget is exhausted.
    ip_allowed = rate_hit(db, settings, "request-ip:" + ip, settings.ip_hourly_limit, now)
    lock_email(db, settings, email)
    email_allowed = rate_hit(
        db, settings, "request-email:" + email, settings.email_hourly_limit, now
    )
    latest = db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.email == email)
        .order_by(LoginChallenge.created_at.desc())
        .limit(1)
    )
    if not ip_allowed or not email_allowed:
        return
    if latest and (now - latest.created_at).total_seconds() < settings.code_resend_seconds:
        return
    if db.scalar(select(User.deleting_at).where(User.email == email)) is not None:
        return
    db.execute(
        update(LoginChallenge)
        .where(LoginChallenge.email == email, LoginChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = LoginChallenge(
        email=email,
        code_digest=digest(settings, "login-code", email + ":" + code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.code_ttl_seconds),
    )
    db.add(challenge)
    db.flush()
    db.add(
        Job(
            kind="login_email",
            deduplication_key=str(challenge.id),
            encrypted_payload=encrypt_job(
                settings,
                {
                    "email": email,
                    "code": code,
                    "challenge_id": str(challenge.id),
                },
            ),
            available_at=now,
            expires_at=challenge.expires_at,
        )
    )


def verify_code(db: Session, settings: Settings, email: str, code: str, ip: str):
    now = datetime.now(UTC)
    if not rate_hit(db, settings, "verify-ip:" + ip, settings.ip_hourly_limit * 5, now):
        return None
    lock_email(db, settings, email)
    challenge = db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.email == email, LoginChallenge.consumed_at.is_(None))
        .order_by(LoginChallenge.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if (
        challenge is None
        or challenge.expires_at <= now
        or challenge.attempts >= settings.code_attempt_limit
    ):
        return None
    challenge.attempts += 1
    if not hmac.compare_digest(
        challenge.code_digest, digest(settings, "login-code", email + ":" + code)
    ):
        return None
    challenge.consumed_at = now
    user = db.scalar(select(User).where(User.email == email).with_for_update())
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()
    if user.deleting_at is not None:
        return None
    token = new_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_digest=digest(settings, "session", token),
            expires_at=now + timedelta(days=settings.session_days),
        )
    )
    return user, token


def principal(request: Request, db: Session):
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if not token or len(token) > 100:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    session = db.scalar(
        select(AuthSession).where(AuthSession.token_digest == digest(settings, "session", token))
    )
    if session is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    # All authenticated work and revocation serialize through the user row.
    user = db.scalar(
        select(User)
        .where(User.id == session.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    db.refresh(session)
    if (
        user is None
        or user.deleting_at is not None
        or session.revoked_at is not None
        or session.expires_at <= datetime.now(UTC)
    ):
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    expected_user = request.headers.get("x-miroven-user-id")
    if expected_user is not None and expected_user != str(user.id):
        raise ApiError(409, "auth_scope_changed", "The signed-in account has changed.")
    return user, session, token


def check_csrf(request: Request, token: str):
    actual = request.headers.get("x-csrf-token", "")
    expected = digest(request.app.state.settings, "csrf", token)
    if not hmac.compare_digest(actual.encode(), expected.encode()):
        raise ApiError(403, "csrf_failed", "Refresh the session before retrying.")
