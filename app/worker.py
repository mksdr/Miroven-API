"""Durable mail delivery. One row stays locked until send/retry state is committed."""

import argparse
import logging.config
import smtplib
import ssl
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from uuid import UUID

from cryptography.fernet import InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.account import process_deletion, retry_deletion
from app.config import get_settings
from app.db import build_engine
from app.maintenance import sweep
from app.models import AccountDeletion, AuthSession, Job, LoginChallenge, RateWindow
from app.observability import LOG_CONFIG
from app.security import decrypt_job
from app.storage import build_storage


def send_email(settings, payload):
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = payload["email"]
    message["Subject"] = "Your Miroven sign-in code"
    message.set_content(
        f"Your Miroven sign-in code is {payload['code']}.\n"
        "It expires shortly. Do not share it with anyone.\n"
        "This code signs in to your account; it cannot unlock an encrypted vault."
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            password = settings.smtp_password.get_secret_value() if settings.smtp_password else ""
            smtp.login(settings.smtp_username, password)
        smtp.send_message(message)


def process_one(engine, settings, sender=send_email) -> bool:
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        job = db.scalar(
            select(Job)
            .where(
                Job.status == "pending",
                Job.available_at <= now,
            )
            .order_by(Job.available_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return False
        if job.kind != "login_email":
            job.status = "failed"
            job.encrypted_payload = None
            return True
        try:
            payload = decrypt_job(settings, job.encrypted_payload)
            challenge_id = UUID(payload["challenge_id"])
        except (InvalidToken, ValueError, TypeError, KeyError):
            job.status = "failed"
            job.encrypted_payload = None
            return True
        challenge = db.get(LoginChallenge, challenge_id)
        if (
            job.expires_at <= now
            or challenge is None
            or challenge.consumed_at is not None
            or challenge.attempts >= settings.code_attempt_limit
        ):
            job.status = "complete"
            job.completed_at = now
            job.encrypted_payload = None
            return True
        job.attempts += 1
        try:
            sender(settings, payload)
        except (smtplib.SMTPException, OSError):
            if job.attempts >= 5:
                job.status = "failed"
                job.encrypted_payload = None
            else:
                job.available_at = now + timedelta(seconds=min(120, 5 * 2**job.attempts))
        else:
            job.status = "complete"
            job.completed_at = now
            job.encrypted_payload = None
    # A crash after SMTP acceptance can resend the same code. Codes remain single-use.
    return True


def cleanup(engine):
    cutoff = datetime.now(UTC) - timedelta(days=1)
    with Session(engine) as db, db.begin():
        db.execute(delete(RateWindow).where(RateWindow.window_start < cutoff))
        db.execute(delete(LoginChallenge).where(LoginChallenge.expires_at < cutoff))
        db.execute(delete(Job).where(Job.expires_at < cutoff))
        db.execute(delete(AuthSession).where(AuthSession.expires_at < cutoff))
        db.execute(
            delete(AccountDeletion).where(
                AccountDeletion.status == "complete", AccountDeletion.expires_at < cutoff
            )
        )


def main():
    logging.config.dictConfig(LOG_CONFIG)
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--retry-deletion", type=UUID)
    args = parser.parse_args()
    settings = get_settings()
    engine = build_engine(settings)
    storage = build_storage(settings)
    if args.retry_deletion:
        try:
            retry_deletion(engine, args.retry_deletion)
        except ValueError:
            raise SystemExit("Deletion task must exist and be failed") from None
        engine.dispose()
        print("deletion_requeued", flush=True)
        return
    last_cleanup = 0.0
    try:
        while True:
            try:
                if time.monotonic() - last_cleanup > 3600:
                    cleanup(engine)
                    sweep(engine, settings, storage)
                    last_cleanup = time.monotonic()
                processed = process_deletion(engine, settings, storage)
                processed = process_one(engine, settings) or processed
            except Exception as exc:
                # Log only the class: SMTP/DB exceptions may contain recipients or credentials.
                print(f"worker_error:{type(exc).__name__}", flush=True)
                if args.once:
                    raise SystemExit(1) from None
                time.sleep(5)
                continue
            if args.once:
                return
            if not processed:
                time.sleep(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"worker_error:{type(exc).__name__}") from None
