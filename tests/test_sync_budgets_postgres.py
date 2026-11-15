"""Account-wide encrypted document/receipt budgets, using real PostgreSQL."""

import base64
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.maintenance import sweep
from app.models import Entry, JournalChange, SyncOperation, SyncState
from app.profile import PROFILE_DOCUMENT_ID
from app.storage import build_storage
from app.sync_budgets import document_size
from app.sync_schemas import SyncWrite
from tests.test_auth_postgres import vault_body
from tests.test_sync_postgres import operation, ready  # noqa: F401

pytestmark = pytest.mark.postgres


def test_repeated_edits_hit_document_budget_with_only_one_entry(api, pg, ready):  # noqa: F811
    api.app.state.settings = api.app.state.settings.model_copy(
        update={"sync_storage_quota_bytes": 1500}
    )
    body = operation()
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    body.update(operation(body["entry_id"], base=1))
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    body.update(operation(body["entry_id"], base=2))
    response = api.post("/api/v1/sync/push", json=body)
    assert response.status_code == 409
    assert response.json()["code"] == "sync_storage_limit"
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 2
        assert db.scalar(select(func.count()).select_from(SyncOperation)) == 2


def test_receipt_limit_keeps_exact_retry_and_operation_mismatch(api, ready):  # noqa: F811
    api.app.state.settings = api.app.state.settings.model_copy(update={"sync_operation_limit": 1})
    first = operation()
    accepted = api.post("/api/v1/sync/push", json=first)
    assert accepted.status_code == 200
    assert api.post("/api/v1/sync/push", json=operation()).json()["code"] == "sync_operation_limit"
    assert api.post("/api/v1/sync/push", json=first).json() == accepted.json()
    changed = copy.deepcopy(first)
    changed["entry_id"] = str(uuid.uuid4())
    assert api.post("/api/v1/sync/push", json=changed).json()["code"] == "operation_mismatch"


def persisted_state(pg, identity):
    with Session(pg) as db:
        return db.get(SyncState, uuid.UUID(identity["user_id"]))


def test_hourly_operation_budget_does_not_charge_replays_and_resets(api, pg, ready):  # noqa: F811
    api.app.state.settings.sync_hourly_operations = 1
    first = operation()
    accepted = api.post("/api/v1/sync/push", json=first)
    assert accepted.status_code == 200
    next_body = operation(first["entry_id"], base=1)
    response = api.post("/api/v1/sync/push", json=next_body)
    assert response.status_code == 429
    assert response.json()["code"] == "sync_rate_limited"
    assert api.post("/api/v1/sync/push", json=first).json() == accepted.json()
    assert persisted_state(pg, ready).write_window_count == 1
    with Session(pg) as db, db.begin():
        state = db.get(SyncState, uuid.UUID(ready["user_id"]))
        state.write_window_start -= timedelta(hours=1)
    assert api.post("/api/v1/sync/push", json=next_body).status_code == 200
    assert persisted_state(pg, ready).write_window_count == 1


def test_hourly_bytes_charge_the_complete_document(api, pg, ready):  # noqa: F811
    # The body is a fixed-size canonical document; one fits, a second does not.
    api.app.state.settings.sync_hourly_bytes = 600
    first = operation()
    assert api.post("/api/v1/sync/push", json=first).status_code == 200
    next_body = operation(first["entry_id"], base=1)
    assert api.post("/api/v1/sync/push", json=next_body).status_code == 429
    state = persisted_state(pg, ready)
    assert state.write_window_count == 1
    assert state.write_window_bytes == document_size(normalized(first))


def normalized(body):
    document = SyncWrite(**body).model_dump(mode="json")
    if document["attachment_version"] == 1 and not document["blob_ids"]:
        document.pop("attachment_version")
        document.pop("blob_ids")
    return document


@pytest.mark.parametrize(
    "budget", ["sync_storage_quota_bytes", "sync_hourly_operations", "sync_operation_limit"]
)
def test_concurrent_writers_cannot_overrun_budget(api, pg, ready, budget):  # noqa: F811
    bodies = [operation() for _ in range(6)]
    value = 4 * document_size(normalized(bodies[0])) if budget == "sync_storage_quota_bytes" else 2
    setattr(api.app.state.settings, budget, value)
    token = api.cookies.get(api.app.state.settings.cookie_name)
    headers = dict(api.headers)

    def send(body):
        with TestClient(api.app, base_url="http://localhost:8000", headers=headers) as device:
            device.cookies.set(api.app.state.settings.cookie_name, token)
            response = device.post("/api/v1/sync/push", json=body)
            return response.status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(send, bodies))
    assert statuses.count(200) == 2
    assert all(status in (200, 409, 429) for status in statuses)
    state = persisted_state(pg, ready)
    assert state.head == 2
    assert state.write_window_count == 2
    if budget == "sync_storage_quota_bytes":
        assert state.document_bytes == value
    else:
        assert state.document_bytes > 0
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(SyncOperation)) == 2


def test_failed_push_does_not_reserve_bytes_or_rate_slots(api, pg, ready):  # noqa: F811
    api.app.state.settings.sync_hourly_operations = 1
    body = operation()
    body["blob_id"] = str(uuid.uuid4())
    assert api.post("/api/v1/sync/push", json=body).json()["code"] == "blob_not_ready"
    state = persisted_state(pg, ready)
    assert state is None or (state.document_bytes, state.write_window_count, state.head) == (
        0,
        0,
        0,
    )
    body.pop("blob_id")
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    assert persisted_state(pg, ready).write_window_count == 1


def test_history_cleanup_releases_only_history_and_keeps_receipts(api, pg, ready):  # noqa: F811
    first = operation()
    first_result = api.post("/api/v1/sync/push", json=first).json()
    second = operation(first["entry_id"], base=1)
    assert api.post("/api/v1/sync/push", json=second).status_code == 200
    before = persisted_state(pg, ready).document_bytes
    # Snapshot reads must remain possible even at the primary storage budget.
    api.app.state.settings.sync_storage_quota_bytes = before
    snapshot = api.get("/api/v1/sync/snapshot")
    assert snapshot.status_code == 200
    with Session(pg) as db, db.begin():
        for row in db.scalars(select(JournalChange)):
            row.created_at = datetime.now(UTC) - timedelta(days=91)
    sweep(pg, api.app.state.settings, build_storage(api.app.state.settings))
    state = persisted_state(pg, ready)
    assert state.document_bytes == document_size(normalized(second))
    assert state.floor == 2
    assert state.head == 2
    assert api.post("/api/v1/sync/push", json=first).json() == first_result
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    assert (
        api.post("/api/v1/sync/push", json=operation(first["entry_id"], base=2)).status_code == 200
    )
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(SyncOperation)) == 3


def test_profile_and_deletes_cannot_bypass_document_budget(api, pg, ready):  # noqa: F811
    body = operation(PROFILE_DOCUMENT_ID)
    api.app.state.settings.sync_storage_quota_bytes = 100
    assert api.post("/api/v1/sync/push", json=body).json()["code"] == "sync_storage_limit"
    assert (
        api.post("/api/v1/sync/push", json=operation(delete=True)).json()["code"]
        == "sync_storage_limit"
    )
    assert persisted_state(pg, ready) is None


def test_lowered_quota_preserves_data_and_allows_net_byte_reduction(api, pg, ready):  # noqa: F811
    body = operation()
    body["payload"]["ciphertext"] = base64.b64encode(bytes(4096)).decode()
    accepted = api.post("/api/v1/sync/push", json=body)
    assert accepted.status_code == 200
    before = persisted_state(pg, ready).document_bytes
    api.app.state.settings.sync_storage_quota_bytes = 1
    assert api.post("/api/v1/sync/push", json=body).json() == accepted.json()
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    assert api.post("/api/v1/sync/push", json=operation()).status_code == 409
    assert (
        api.post(
            "/api/v1/sync/push", json=operation(body["entry_id"], base=1, delete=True)
        ).status_code
        == 200
    )
    assert persisted_state(pg, ready).document_bytes < before


def test_budget_usage_is_separate_from_blob_usage_and_accounts(api, pg, ready, sign_in):  # noqa: F811
    api.app.state.settings.sync_hourly_operations = 1
    assert api.post("/api/v1/sync/push", json=operation()).status_code == 200
    assert api.get("/api/v1/account/usage").json()["storage_bytes"] == 0
    other, _ = sign_in(api, email="two@example.com")
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    assert api.post("/api/v1/sync/push", json=operation()).status_code == 200
    assert persisted_state(pg, ready).head == 1
    assert persisted_state(pg, other).head == 1
