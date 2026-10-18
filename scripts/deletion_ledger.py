"""Export deletion IDs separately from backups; reapply before exposing a restored DB."""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402
from app.db import build_engine  # noqa: E402
from app.models import AccountDeletion, AuthSession, User  # noqa: E402
from app.security import digest, new_token  # noqa: E402


def export_ledger(engine):
    with Session(engine) as db:
        return {
            "format": 1,
            "deletion_ids": [str(value) for value in db.scalars(select(AccountDeletion.id))],
        }


def apply_ledger(engine, settings, ledger):
    if ledger.get("format") != 1:
        raise ValueError("Unsupported deletion ledger")
    ids = [UUID(value) for value in ledger["deletion_ids"]]
    now = datetime.now(UTC)
    affected = 0
    with Session(engine) as db, db.begin():
        for user_id in ids:
            user = db.get(User, user_id, with_for_update=True)
            if user is None:
                continue
            user.deleting_at = user.deleting_at or now
            db.execute(
                update(AuthSession).where(AuthSession.user_id == user_id).values(revoked_at=now)
            )
            task = db.get(AccountDeletion, user_id)
            if task is None:
                task = AccountDeletion(
                    id=user_id,
                    token_digest=digest(settings, "deletion", new_token()),
                    expires_at=now + timedelta(days=30),
                )
                db.add(task)
            task.user_id = user_id
            task.status = "pending"
            task.attempts = 0
            task.available_at = now
            task.completed_at = None
            affected += 1
    return affected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "apply"])
    args = parser.parse_args()
    engine = None
    try:
        settings = get_settings()
        engine = build_engine(settings)
        if args.action == "export":
            print(json.dumps(export_ledger(engine)))
        else:
            print(f"deletions_reapplied:{apply_ledger(engine, settings, json.load(sys.stdin))}")
    except Exception as exc:
        raise SystemExit(f"ledger_failed:{type(exc).__name__}") from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
