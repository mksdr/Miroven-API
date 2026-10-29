import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Text, cast, func, inspect, select, text
from sqlalchemy.orm import Session

from app.models import Entry, JournalChange, SyncSnapshot, SyncState
from app.sync_budgets import document_size
from tests.test_sync_postgres import operation, ready  # noqa: F401

pytestmark = pytest.mark.postgres


def test_initial_migration_roundtrip_and_model_consistency(pg, postgres_settings):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["settings"] = postgres_settings
    command.downgrade(config, "base")
    assert inspect(pg).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    command.check(config)


def test_sync_budgets_backfill_existing_content_and_preserve_receipts(api, pg, ready):  # noqa: F811
    first = operation()
    accepted = api.post("/api/v1/sync/push", json=first)
    assert accepted.status_code == 200
    second = operation(first["entry_id"], base=1)
    second.update(attachment_version=2, blob_ids=[])
    assert api.post("/api/v1/sync/push", json=second).status_code == 200
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    user_id = uuid.UUID(ready["user_id"])
    with Session(pg) as db:
        old_epoch = db.get(SyncState, user_id).epoch
        before = list(db.execute(text("SELECT user_id, id, revision, document FROM entries")))
        receipts = list(
            db.execute(text("SELECT user_id, id, request_digest, result FROM sync_operations"))
        )
        expected = sum(
            db.scalar(
                select(func.coalesce(func.sum(func.octet_length(cast(model.document, Text))), 0))
            )
            for model in (Entry, JournalChange)
        )
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["settings"] = api.app.state.settings
    command.downgrade(config, "0005_subscriptions")
    assert "document_bytes" not in {c["name"] for c in inspect(pg).get_columns("sync_states")}
    command.upgrade(config, "head")
    command.check(config)
    with Session(pg) as db:
        state = db.get(SyncState, user_id)
        assert state.document_bytes == expected
        assert state.epoch == old_epoch
        assert state.head == 2
        assert state.write_window_count == state.write_window_bytes == 0
        assert (
            list(db.execute(text("SELECT user_id, id, revision, document FROM entries"))) == before
        )
        assert (
            list(
                db.execute(text("SELECT user_id, id, request_digest, result FROM sync_operations"))
            )
            == receipts
        )
        assert db.scalar(select(func.count()).select_from(SyncSnapshot)) == 1
        assert expected == sum(
            document_size(row.document)
            for model in (Entry, JournalChange)
            for row in db.scalars(select(model))
        )
    # Grandfather existing data without deleting it, but refuse additional growth.
    api.app.state.settings.sync_storage_quota_bytes = 1
    assert api.post("/api/v1/sync/push", json=operation()).json()["code"] == "sync_storage_limit"
    assert api.post("/api/v1/sync/push", json=first).json() == accepted.json()
    assert api.get("/health/ready").status_code == 200
