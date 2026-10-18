import base64
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.account import process_deletion
from app.models import BlobObject, Entry, JournalChange, SnapshotItem, SyncOperation, SyncSnapshot
from app.profile import PROFILE_DOCUMENT_ID
from app.storage import build_storage
from tests.test_account_postgres import fresh_code, request_delete
from tests.test_auth_postgres import vault_body
from tests.test_sync_postgres import operation, upload

pytestmark = pytest.mark.postgres


@pytest.fixture
def ready(api, sign_in):
    identity, _ = sign_in(api)
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    return identity


def test_profile_retries_changes_snapshot_and_tombstone(api, ready):
    body = operation(PROFILE_DOCUMENT_ID)
    body.update(attachment_version=2, blob_ids=[])
    response = api.post("/api/v1/sync/push", json=body)
    assert response.status_code == 200, response.text
    assert api.post("/api/v1/sync/push", json=body).json() == response.json()
    changed = copy.deepcopy(body)
    changed["payload"]["ciphertext"] = base64.b64encode(bytes(32)).decode()
    assert api.post("/api/v1/sync/push", json=changed).json()["code"] == "operation_mismatch"
    changes = api.get("/api/v1/sync/changes").json()
    assert len(changes["items"]) == 1
    assert changes["items"][0]["payload"] == body["payload"]
    snapshot = api.get("/api/v1/sync/snapshot").json()
    assert snapshot["items"][0]["entry_id"] == str(PROFILE_DOCUMENT_ID)
    deleted = operation(PROFILE_DOCUMENT_ID, base=1, delete=True)
    deleted.update(attachment_version=2)
    assert api.post("/api/v1/sync/push", json=deleted).status_code == 200
    stale = operation(PROFILE_DOCUMENT_ID, base=1)
    assert api.post("/api/v1/sync/push", json=stale).json()["code"] == "revision_conflict"
    delta = api.get("/api/v1/sync/changes", params={"cursor": changes["next_cursor"]}).json()
    assert delta["items"][0]["change_type"] == "delete"
    assert delta["items"][0]["revision"] == 2
    assert api.get("/api/v1/sync/snapshot").json()["items"][0]["change_type"] == "delete"
    restored = operation(PROFILE_DOCUMENT_ID, base=2)
    restored.update(attachment_version=2)
    assert api.post("/api/v1/sync/push", json=restored).status_code == 200


@pytest.mark.parametrize("profile_first", [False, True])
def test_profile_never_consumes_journal_quota_including_tombstones(api, ready, profile_first):
    api.app.state.settings.max_entries = 1
    journal = operation()
    profile = operation(PROFILE_DOCUMENT_ID)
    for body in [profile, journal] if profile_first else [journal, profile]:
        assert api.post("/api/v1/sync/push", json=body).status_code == 200
    usage = api.get("/api/v1/account/usage").json()
    assert usage["entries"] == 1
    assert usage["storage_bytes"] == 0
    assert api.post("/api/v1/sync/push", json=operation()).json()["code"] == "entry_limit"
    for body in (
        operation(PROFILE_DOCUMENT_ID, base=1),
        operation(journal["entry_id"], base=1, delete=True),
        operation(PROFILE_DOCUMENT_ID, base=2, delete=True),
        operation(PROFILE_DOCUMENT_ID, base=3),
    ):
        assert api.post("/api/v1/sync/push", json=body).status_code == 200
    assert api.get("/api/v1/account/usage").json()["entries"] == 1
    assert api.post("/api/v1/sync/push", json=operation()).json()["code"] == "entry_limit"


def test_profile_blob_rejection_has_no_storage_or_sync_side_effects(api, pg, ready):
    response = upload(api, PROFILE_DOCUMENT_ID)
    assert response.status_code == 422
    assert response.json()["code"] == "profile_attachment_forbidden"
    journal = operation()
    blob_id = upload(api, journal["entry_id"]).json()["blob_id"]
    for attachments in ({"blob_id": blob_id}, {"attachment_version": 2, "blob_ids": [blob_id]}):
        body = {**operation(PROFILE_DOCUMENT_ID), **attachments}
        assert api.post("/api/v1/sync/push", json=body).status_code == 422
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 0
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 0
        assert db.scalar(select(func.count()).select_from(SyncOperation)) == 0
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1
        assert db.get(BlobObject, (UUID(ready["user_id"]), UUID(blob_id))).status == "ready"


def test_profile_is_account_scoped_and_cursors_do_not_cross_accounts(api, ready, sign_in):
    body = operation(PROFILE_DOCUMENT_ID)
    response = api.post("/api/v1/sync/push", json=body)
    assert response.status_code == 200
    other, _ = sign_in(api, "other-profile@example.com")
    assert other["user_id"] != ready["user_id"]
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    assert api.get("/api/v1/sync/changes").json()["items"] == []
    assert api.get("/api/v1/sync/snapshot").json()["items"] == []
    assert (
        api.get("/api/v1/sync/changes", params={"cursor": response.json()["cursor"]}).status_code
        == 400
    )
    assert api.post("/api/v1/sync/push", json=operation(PROFILE_DOCUMENT_ID)).status_code == 200


def test_concurrent_profile_writes_have_one_winner_and_one_document(api, pg, ready):
    cookies, headers = dict(api.cookies), dict(api.headers)

    def write(_):
        with TestClient(api.app, base_url="http://localhost:8000") as device:
            device.cookies.update(cookies)
            device.headers.update(headers)
            return device.post("/api/v1/sync/push", json=operation(PROFILE_DOCUMENT_ID)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2))) == [200, 409]
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 1
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 1


def test_profile_http_body_boundary(api, ready):
    body = operation(PROFILE_DOCUMENT_ID)
    body["payload"]["ciphertext"] = base64.b64encode(bytes(65536)).decode()
    raw = json.dumps(body).encode()
    content = raw + b" " * (131072 - len(raw))
    assert (
        api.post(
            "/api/v1/sync/push", content=content, headers={"Content-Type": "application/json"}
        ).status_code
        == 200
    )
    assert (
        api.post(
            "/api/v1/sync/push",
            content=content + b" ",
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_account_deletion_removes_profile_history_and_snapshots(api, pg, postgres_settings, ready):
    assert api.post("/api/v1/sync/push", json=operation(PROFILE_DOCUMENT_ID)).status_code == 200
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    assert request_delete(api, fresh_code(api, pg, postgres_settings)).status_code == 202
    assert process_deletion(pg, api.app.state.settings, build_storage(api.app.state.settings))
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 0
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 0
        assert db.scalar(select(func.count()).select_from(SnapshotItem)) == 0
        assert db.scalar(select(func.count()).select_from(SyncSnapshot)) == 0
    assert api.get("/api/v1/auth/session").status_code == 401
