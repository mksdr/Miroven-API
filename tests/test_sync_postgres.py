import base64
import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app import sync
from app.maintenance import sweep
from app.models import AuthSession, BlobObject, JournalChange, SyncSnapshot, SyncState, User
from app.profile import PROFILE_DOCUMENT_ID
from app.storage import build_storage
from app.sync_schemas import SyncWrite
from tests.test_auth_postgres import vault_body

pytestmark = pytest.mark.postgres


def operation(entry_id=None, base=0, delete=False):
    return {
        "operation_id": str(uuid.uuid4()),
        "entry_id": str(entry_id or uuid.uuid4()),
        "base_revision": base,
        "revision": base + 1,
        "change_type": "delete" if delete else "upsert",
        **(
            {}
            if delete
            else {
                "payload": {
                    "ciphertext": base64.b64encode(b"ciphertext-with-gcm-tag" * 3).decode(),
                    "content_nonce": base64.b64encode(bytes(12)).decode(),
                    "wrapped_data_key": base64.b64encode(bytes(48)).decode(),
                    "key_nonce": base64.b64encode(bytes(12)).decode(),
                    "crypto_version": 1,
                }
            }
        ),
    }


@pytest.fixture
def ready(api, sign_in):
    identity, _code = sign_in(api)
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    return identity


def upload(api, entry_id, data=b"opaque-encrypted-file", blob_id=None, revision=1):
    return api.post(
        "/api/v1/blobs",
        params={
            "blob_id": str(blob_id or uuid.uuid4()),
            "entry_id": str(entry_id),
            "revision": revision,
        },
        content=data,
        headers={"Content-Type": "application/octet-stream"},
    )


def age_history(db):
    for row in db.scalars(select(JournalChange)):
        row.created_at = datetime.now(UTC) - timedelta(days=91)
    for row in db.scalars(select(BlobObject)):
        row.created_at = datetime.now(UTC) - timedelta(days=2)


def test_four_attachments_roundtrip_retry_snapshot_and_old_client_guard(api, pg, ready):
    body = operation()
    body.update(
        attachment_version=2,
        blob_ids=[
            upload(api, body["entry_id"], data=f"photo-{i}".encode()).json()["blob_id"]
            for i in range(4)
        ],
    )
    response = api.post("/api/v1/sync/push", json=body)
    assert response.status_code == 200, response.text
    assert api.post("/api/v1/sync/push", json=body).json() == response.json()
    changes = api.get("/api/v1/sync/changes").json()["items"]
    assert changes[0]["blob_ids"] == body["blob_ids"]
    snapshot = api.get("/api/v1/sync/snapshot").json()["items"]
    assert snapshot[0]["blob_ids"] == body["blob_ids"]
    for i, blob_id in enumerate(body["blob_ids"]):
        assert api.get(f"/api/v1/blobs/{blob_id}").content == f"photo-{i}".encode()
    stale = operation(body["entry_id"], base=1)
    assert api.post("/api/v1/sync/push", json=stale).json()["code"] == "client_update_required"
    stale.update(attachment_version=2, blob_ids=[])
    assert api.post("/api/v1/sync/push", json=stale).status_code == 200
    with Session(pg) as db, db.begin():
        age_history(db)
    sweep(pg, api.app.state.settings, build_storage(api.app.state.settings))
    # The frozen snapshot still protects all four attachments after history expires.
    for blob_id in body["blob_ids"]:
        assert api.get(f"/api/v1/blobs/{blob_id}").status_code == 200
    with Session(pg) as db, db.begin():
        for row in db.scalars(select(SyncSnapshot)):
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sweep(pg, api.app.state.settings, build_storage(api.app.state.settings))
    for blob_id in body["blob_ids"]:
        assert api.get(f"/api/v1/blobs/{blob_id}").status_code == 404


def test_attachment_list_validation_and_atomic_linking(api, pg, ready):
    body = operation()
    first = upload(api, body["entry_id"]).json()["blob_id"]
    body.update(attachment_version=2, blob_ids=[first, str(uuid.uuid4())])
    assert api.post("/api/v1/sync/push", json=body).json()["code"] == "blob_not_ready"
    with Session(pg) as db:
        assert db.get(BlobObject, (uuid.UUID(ready["user_id"]), uuid.UUID(first))).status == "ready"
    for invalid in ([first] * 2, [str(uuid.uuid4()) for _ in range(5)]):
        body["blob_ids"] = invalid
        assert api.post("/api/v1/sync/push", json=body).status_code == 422
    body["blob_ids"] = [first]
    body["blob_id"] = first
    assert api.post("/api/v1/sync/push", json=body).status_code == 422
    body.pop("blob_id")
    body["attachment_version"] = 1
    assert api.post("/api/v1/sync/push", json=body).status_code == 422
    body["attachment_version"] = 2
    assert api.post("/api/v1/sync/push", json=body).status_code == 200


def test_push_retry_is_idempotent_and_reuse_with_changed_payload_is_rejected(api, pg, ready):
    body = operation()
    first = api.post("/api/v1/sync/push", json=body)
    assert first.status_code == 200
    other = api.post("/api/v1/sync/push", json=operation())
    assert other.status_code == 200
    assert api.post("/api/v1/sync/push", json=body).json() == first.json()
    changed = copy.deepcopy(body)
    changed["payload"]["ciphertext"] = base64.b64encode(bytes(48)).decode()
    response = api.post("/api/v1/sync/push", json=changed)
    assert response.status_code == 409
    assert response.json()["code"] == "operation_mismatch"
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 2


def test_pagination_delete_and_resume_cursor(api, ready):
    first = operation()
    assert api.post("/api/v1/sync/push", json=first).status_code == 200
    updated = operation(first["entry_id"], base=1)
    deleted = operation(first["entry_id"], base=2, delete=True)
    for body in (updated, deleted):
        assert api.post("/api/v1/sync/push", json=body).status_code == 200
    collected = []
    params = {"limit": 1}
    for _ in range(3):
        response = api.get("/api/v1/sync/changes", params=params)
        assert response.status_code == 200
        page = response.json()
        collected.extend(page["items"])
        params["cursor"] = page["next_cursor"]
    assert [item["revision"] for item in collected] == [1, 2, 3]
    assert collected[-1]["change_type"] == "delete"
    assert collected[-1]["payload"] is None
    assert api.get("/api/v1/sync/changes", params=params).json()["items"] == []


def test_concurrent_same_revision_has_one_winner(api, ready):
    entry_id = uuid.uuid4()
    token = api.cookies.get(api.app.state.settings.cookie_name)

    def write(_):
        with TestClient(api.app, base_url="http://localhost:8000") as device:
            device.cookies.set(api.app.state.settings.cookie_name, token)
            return device.post(
                "/api/v1/sync/push",
                json=operation(entry_id),
                headers={
                    "Origin": api.app.state.settings.public_origin,
                    "X-CSRF-Token": ready["csrf_token"],
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, range(2))) == [200, 409]
    assert len(api.get("/api/v1/sync/changes").json()["items"]) == 1


def test_snapshot_pages_keep_original_state_while_other_device_writes(api, ready):
    ids = [uuid.UUID(int=i) for i in (1, 2, 3)]
    for entry_id in ids:
        assert api.post("/api/v1/sync/push", json=operation(entry_id)).status_code == 200
    first = api.get("/api/v1/sync/snapshot", params={"limit": 1}).json()
    assert first["has_more"]
    assert api.post("/api/v1/sync/push", json=operation(ids[1], 1)).status_code == 200
    assert api.post("/api/v1/sync/push", json=operation(ids[2], 1, delete=True)).status_code == 200
    remainder = api.get("/api/v1/sync/snapshot", params={"cursor": first["next_cursor"]}).json()
    assert [(item["revision"], item["change_type"]) for item in remainder["items"]] == [
        (1, "upsert"),
        (1, "upsert"),
    ]
    assert remainder["resume_cursor"] == first["resume_cursor"]
    assert remainder["next_cursor"] is None
    delta = api.get("/api/v1/sync/changes", params={"cursor": remainder["resume_cursor"]}).json()
    assert [item["revision"] for item in delta["items"]] == [2, 2]


def test_cursor_tampering_cross_account_and_generation_are_rejected(api, pg, ready, sign_in):
    response = api.post("/api/v1/sync/push", json=operation())
    cursor = response.json()["cursor"]
    tampered = cursor[:-1] + ("a" if cursor[-1] != "a" else "b")
    assert api.get("/api/v1/sync/changes", params={"cursor": tampered}).status_code == 400
    with Session(pg) as db, db.begin():
        db.get(SyncState, uuid.UUID(ready["user_id"])).epoch = uuid.uuid4()
    assert api.get("/api/v1/sync/changes", params={"cursor": cursor}).status_code == 410
    sign_in(api, "two@example.com")
    assert api.get("/api/v1/sync/changes", params={"cursor": cursor}).status_code == 400
    assert api.get("/api/v1/sync/changes").json()["items"] == []


@pytest.mark.parametrize("profile", [False, True])
def test_expired_history_requires_snapshot_without_losing_current_data(api, pg, ready, profile):
    body = operation(PROFILE_DOCUMENT_ID if profile else None)
    first = api.post("/api/v1/sync/push", json=body).json()
    with Session(pg) as db, db.begin():
        age_history(db)
    sweep(pg, api.app.state.settings, build_storage(api.app.state.settings))
    assert api.get("/api/v1/sync/changes").status_code == 410
    snapshot = api.get("/api/v1/sync/snapshot").json()
    assert snapshot["items"][0]["entry_id"] == body["entry_id"]
    assert (
        api.get("/api/v1/sync/changes", params={"cursor": snapshot["resume_cursor"]}).status_code
        == 200
    )
    assert api.post("/api/v1/sync/push", json=body).json() == first


def test_expired_snapshot_returns_actionable_error(api, pg, ready):
    for _ in range(2):
        api.post("/api/v1/sync/push", json=operation())
    page = api.get("/api/v1/sync/snapshot", params={"limit": 1}).json()
    with Session(pg) as db, db.begin():
        db.scalar(select(SyncSnapshot)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    response = api.get("/api/v1/sync/snapshot", params={"cursor": page["next_cursor"]})
    assert response.status_code == 410
    assert response.json()["code"] == "snapshot_expired"


def test_linked_blob_roundtrip_owner_isolation_and_immutable_retry(
    api, pg, ready, object_store, sign_in
):
    api.app.state.storage = object_store
    body = operation()
    blob_id = uuid.uuid4()
    data = b"encrypted-photo-envelope" * 10000
    receipt = upload(api, body["entry_id"], data, blob_id)
    assert receipt.status_code == 200
    assert api.get(f"/api/v1/blobs/{blob_id}").status_code == 404
    body["blob_id"] = str(blob_id)
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    assert api.get(f"/api/v1/blobs/{blob_id}").content == data
    assert upload(api, body["entry_id"], data, blob_id).json() == receipt.json()
    assert upload(api, body["entry_id"], data + b"changed", blob_id).status_code == 409
    sign_in(api, "two@example.com")
    assert api.get(f"/api/v1/blobs/{blob_id}").status_code == 404
    foreign = operation()
    foreign["blob_id"] = str(blob_id)
    api.put("/api/v1/vault", json=vault_body())
    assert api.post("/api/v1/sync/push", json=foreign).json()["code"] == "blob_not_ready"


def test_file_write_failure_is_durable_and_retry_can_complete(
    api, pg, ready, object_store, monkeypatch
):
    api.app.state.storage = object_store
    body = operation()
    blob_id = uuid.uuid4()
    put = object_store.put

    def lose_response(*args):
        put(*args)
        raise OSError("Simulated failure after file storage completed")

    monkeypatch.setattr(object_store, "put", lose_response)
    assert upload(api, body["entry_id"], blob_id=blob_id).status_code == 503
    with Session(pg) as db:
        assert db.scalar(select(BlobObject)).status == "pending"
    monkeypatch.setattr(object_store, "put", put)
    assert upload(api, body["entry_id"], blob_id=blob_id).status_code == 200
    body["blob_id"] = str(blob_id)
    assert api.post("/api/v1/sync/push", json=body).status_code == 200


def test_upload_stream_size_quota_and_csrf_are_enforced(api, pg, ready):
    settings = api.app.state.settings
    settings.blob_max_bytes = 32
    settings.storage_quota_bytes = 40
    body = operation()
    assert upload(api, body["entry_id"], b"x" * 33).status_code == 413
    assert upload(api, body["entry_id"], b"x" * 25).status_code == 200
    assert upload(api, body["entry_id"], b"x" * 25).json()["code"] == "storage_limit"
    assert upload(api, body["entry_id"], b"").status_code == 422
    api.headers.pop("X-CSRF-Token")
    assert upload(api, body["entry_id"], b"x").status_code == 403
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1


def test_blob_metadata_must_match_entry_and_revision(api, ready):
    body = operation()
    receipt = upload(api, uuid.uuid4()).json()
    body["blob_id"] = receipt["blob_id"]
    assert api.post("/api/v1/sync/push", json=body).json()["code"] == "blob_not_ready"


def test_gc_keeps_snapshot_files_and_retries_failed_delete(
    api, pg, ready, object_store, monkeypatch
):
    api.app.state.storage = object_store
    body = operation()
    receipt = upload(api, body["entry_id"]).json()
    body["blob_id"] = receipt["blob_id"]
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    api.get("/api/v1/sync/snapshot")
    assert api.post("/api/v1/sync/push", json=operation(body["entry_id"], 1)).status_code == 200
    with Session(pg) as db, db.begin():
        age_history(db)
    sweep(pg, api.app.state.settings, object_store)
    assert api.get(f"/api/v1/blobs/{receipt['blob_id']}").status_code == 200
    with Session(pg) as db, db.begin():
        db.scalar(select(SyncSnapshot)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    remove = object_store.delete

    def unavailable(*_):
        raise OSError("Storage is unavailable")

    monkeypatch.setattr(object_store, "delete", unavailable)
    sweep(pg, api.app.state.settings, object_store)
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1
    monkeypatch.setattr(object_store, "delete", remove)
    sweep(pg, api.app.state.settings, object_store)
    assert api.get(f"/api/v1/blobs/{receipt['blob_id']}").status_code == 404


def test_pruning_does_not_skip_a_fresh_change(api, pg, ready):
    for _ in range(2):
        api.post("/api/v1/sync/push", json=operation())
    with Session(pg) as db, db.begin():
        rows = list(db.scalars(select(JournalChange).order_by(JournalChange.cursor)))
        rows[1].created_at = datetime.now(UTC) - timedelta(days=91)
    sweep(pg, api.app.state.settings, build_storage(api.app.state.settings))
    assert len(api.get("/api/v1/sync/changes").json()["items"]) == 2


@pytest.mark.parametrize("mutation", ["no_payload", "delete_payload", "revision_gap", "plaintext"])
def test_sync_rejects_invalid_envelopes_and_revisions(api, ready, mutation):
    body = operation()
    if mutation == "no_payload":
        body.pop("payload")
    elif mutation == "delete_payload":
        body["change_type"] = "delete"
    elif mutation == "revision_gap":
        body["revision"] = 2
    else:
        body["text"] = "Never accept unencrypted journal text"
    assert api.post("/api/v1/sync/push", json=body).status_code == 422


def test_push_commit_failure_rolls_back_cursor_entry_and_file_link(api, pg, ready):
    body = operation()
    body["blob_id"] = upload(api, body["entry_id"]).json()["blob_id"]

    def fail_commit(db):
        raise RuntimeError("Simulated lost transaction")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError):
            api.post("/api/v1/sync/push", json=body)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(JournalChange)) == 0
        assert db.scalar(select(BlobObject)).status == "ready"
        assert db.get(SyncState, uuid.UUID(ready["user_id"])) is None
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    with Session(pg) as db:
        state = db.get(SyncState, uuid.UUID(ready["user_id"]))
        assert state.write_window_count == 1
        assert state.document_bytes > 0
    assert len(api.get("/api/v1/sync/changes").json()["items"]) == 1


def test_reader_waits_for_inflight_commit_so_cursor_cannot_skip_it(api, pg, ready):
    inserted, allow_commit, reading = Event(), Event(), Event()
    token = api.cookies.get(api.app.state.settings.cookie_name)

    def observe_lock(connection, cursor, statement, parameters, context, many):
        if inserted.is_set() and "FROM users" in statement and "FOR UPDATE" in statement:
            reading.set()

    def slow_writer():
        with Session(pg) as db, db.begin():
            user_id = uuid.UUID(ready["user_id"])
            db.scalar(select(User).where(User.id == user_id).with_for_update())
            sync.push(db, api.app.state.settings, user_id, SyncWrite(**operation()))
            inserted.set()
            assert allow_commit.wait(5)

    def reader():
        assert inserted.wait(5)
        with TestClient(api.app, base_url="http://localhost:8000") as device:
            device.cookies.set(api.app.state.settings.cookie_name, token)
            return device.get("/api/v1/sync/changes").json()

    event.listen(api.app.state.engine, "before_cursor_execute", observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            writer = executor.submit(slow_writer)
            future = executor.submit(reader)
            try:
                assert reading.wait(5)
            finally:
                allow_commit.set()
            writer.result(timeout=5)
            assert len(future.result(timeout=5)["items"]) == 1
    finally:
        event.remove(api.app.state.engine, "before_cursor_execute", observe_lock)


def test_pending_orphan_is_removed_but_recent_upload_survives(api, pg, ready, object_store):
    api.app.state.storage = object_store
    old = upload(api, uuid.uuid4()).json()
    with Session(pg) as db, db.begin():
        db.scalar(select(BlobObject)).created_at = datetime.now(UTC) - timedelta(days=2)
    recent = upload(api, uuid.uuid4()).json()
    sweep(pg, api.app.state.settings, object_store)
    with Session(pg) as db:
        assert db.get(BlobObject, (uuid.UUID(ready["user_id"]), uuid.UUID(old["blob_id"]))) is None
        assert db.get(BlobObject, (uuid.UUID(ready["user_id"]), uuid.UUID(recent["blob_id"])))


def test_snapshot_limit_and_large_cursors(api, pg, ready):
    api.app.state.settings.max_snapshots = 1
    # Exercise opaque cursor precision independently of the ordinary lifetime cap.
    api.app.state.settings.sync_operation_limit = 2**63 - 1
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    assert api.get("/api/v1/sync/snapshot").status_code == 429
    with Session(pg) as db, db.begin():
        db.get(SyncState, uuid.UUID(ready["user_id"])).head = 2**53 + 100
    response = api.post("/api/v1/sync/push", json=operation())
    assert response.status_code == 200
    cursor = response.json()["cursor"]
    assert isinstance(cursor, str)
    assert api.get("/api/v1/sync/changes", params={"cursor": cursor}).status_code == 200


def test_revocation_during_upload_prevents_storage_write(api, pg, ready, monkeypatch):
    from app import sync_routes

    original = sync_routes.save_blob

    def revoke_before_saving(*args):
        with Session(pg) as db, db.begin():
            db.scalar(select(AuthSession)).revoked_at = datetime.now(UTC)
        return original(*args)

    monkeypatch.setattr(sync_routes, "save_blob", revoke_before_saving)
    assert upload(api, uuid.uuid4()).status_code == 401
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 0
