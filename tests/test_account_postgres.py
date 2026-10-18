import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Event
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.account import process_deletion, retry_deletion
from app.models import AccountDeletion, AuthSession, BlobObject, Job, LoginChallenge, User
from tests.conftest import issued_code
from tests.test_auth_postgres import vault_body
from tests.test_sync_postgres import operation, upload

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("reason", ["expired", "exhausted", "wrong"])
def test_reauthentication_failures_never_start_deletion(
    api,
    pg,
    postgres_settings,
    sign_in,
    reason,
):
    sign_in(api)
    code = fresh_code(api, pg, postgres_settings)
    with Session(pg) as db, db.begin():
        challenge = db.scalar(select(LoginChallenge).where(LoginChallenge.consumed_at.is_(None)))
        if reason == "expired":
            challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if reason == "exhausted":
            challenge.attempts = postgres_settings.code_attempt_limit
    if reason == "wrong":
        code = "000000" if code != "000000" else "000001"
    assert request_delete(api, code).status_code == 401
    assert api.get("/api/v1/auth/session").status_code == 200
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(AccountDeletion)) == 0


def test_upload_started_before_deletion_cannot_write_after_deletion(
    api,
    pg,
    postgres_settings,
    sign_in,
    monkeypatch,
):
    from app import sync_routes

    sign_in(api)
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    code = fresh_code(api, pg, postgres_settings)
    authorized, proceed = Event(), Event()
    original = sync_routes.authorize_upload

    def pause_after_authorization(request, db, entry_id):
        original(request, db, entry_id)
        authorized.set()
        assert proceed.wait(10)

    monkeypatch.setattr(sync_routes, "authorize_upload", pause_after_authorization)
    cookies, headers = dict(api.cookies), dict(api.headers)

    def uploading():
        with TestClient(api.app, base_url="http://localhost:8000") as other:
            other.cookies.update(cookies)
            other.headers.update(headers)
            return upload(other, str(uuid4())).status_code

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(uploading)
        try:
            assert authorized.wait(10)
            assert request_delete(api, code).status_code == 202
        finally:
            proceed.set()
        assert future.result(timeout=10) == 401
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 0


def test_latest_deletion_ledger_blocks_users_from_older_backups(
    api,
    pg,
    postgres_settings,
    sign_in,
):
    from scripts.deletion_ledger import apply_ledger, export_ledger

    identity, _ = sign_in(api)
    user_id = UUID(identity["user_id"])
    assert (
        apply_ledger(
            pg,
            postgres_settings,
            {
                "format": 1,
                "deletion_ids": [str(user_id)],
            },
        )
        == 1
    )
    assert api.get("/api/v1/auth/session").status_code == 401
    assert str(user_id) in export_ledger(pg)["deletion_ids"]
    from app.storage import LocalStorage

    assert process_deletion(
        pg, postgres_settings, LocalStorage(api.app.state.settings.blob_directory)
    )
    assert apply_ledger(pg, postgres_settings, export_ledger(pg)) == 0


def test_delete_cascades_linked_entries_history_snapshots_and_large_manifest(
    api,
    pg,
    postgres_settings,
    sign_in,
):
    from app.models import Base
    from app.storage import LocalStorage

    identity, _ = sign_in(api)
    user_id = UUID(identity["user_id"])
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 200
    body = operation()
    blob = upload(api, body["entry_id"]).json()
    body["blob_id"] = blob["blob_id"]
    assert api.post("/api/v1/sync/push", json=body).status_code == 200
    assert api.get("/api/v1/sync/snapshot").status_code == 200
    with Session(pg) as db, db.begin():
        for _ in range(100):
            db.add(
                BlobObject(
                    user_id=user_id,
                    id=uuid4(),
                    entry_id=uuid4(),
                    revision=1,
                    storage_key=f"{user_id.hex}/{uuid4().hex}",
                    size=1,
                    sha256="a" * 64,
                    status="pending",
                )
            )
    assert request_delete(api, fresh_code(api, pg, postgres_settings)).status_code == 202
    storage = LocalStorage(api.app.state.settings.blob_directory)
    assert process_deletion(pg, postgres_settings, storage)
    with Session(pg) as db:
        assert db.get(User, user_id) is not None
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1
    assert process_deletion(pg, postgres_settings, storage)
    with Session(pg) as db:
        for table in Base.metadata.sorted_tables:
            if table.name not in {"account_deletions", "rate_windows"}:
                assert db.scalar(select(func.count()).select_from(table)) == 0, table.name
    assert not list(storage.root.rglob("*.tmp"))


def test_exhausted_storage_retries_can_be_requeued_and_receipt_expires(
    api,
    pg,
    postgres_settings,
    sign_in,
):
    sign_in(api)
    receipt = request_delete(api, fresh_code(api, pg, postgres_settings)).json()
    task_id = UUID(receipt["deletion_id"])
    with Session(pg) as db, db.begin():
        task = db.get(AccountDeletion, task_id)
        db.add(
            BlobObject(
                user_id=task.user_id,
                id=uuid4(),
                entry_id=uuid4(),
                revision=1,
                storage_key=f"{task.user_id.hex}/{uuid4().hex}",
                size=1,
                sha256="a" * 64,
                status="pending",
            )
        )
        task.attempts = 9

    class Broken:
        def delete(self, key):
            raise OSError()

    assert process_deletion(pg, postgres_settings, Broken())
    with Session(pg) as db, db.begin():
        task = db.get(AccountDeletion, task_id)
        assert task.status == "failed" and task.attempts == 10
        task.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not process_deletion(pg, postgres_settings, Broken())
    retry_deletion(pg, task_id)
    with Session(pg) as db:
        task = db.get(AccountDeletion, task_id)
        assert task.status == "pending" and task.attempts == 0
    with pytest.raises(ValueError):
        retry_deletion(pg, task_id)
    assert (
        api.get(
            "/api/v1/account/deletion",
            headers={
                "Authorization": "Bearer " + receipt["deletion_token"],
            },
        ).status_code
        == 401
    )


def fresh_code(api, pg, settings):
    with Session(pg) as db, db.begin():
        db.execute(
            update(LoginChallenge).values(created_at=datetime.now(UTC) - timedelta(minutes=2))
        )
    assert (
        api.post("/api/v1/auth/code/request", json={"email": "one@example.com"}).status_code == 202
    )
    return issued_code(pg, settings, "one@example.com")


def request_delete(api, code):
    return api.request("DELETE", "/api/v1/account", json={"code": code})


def test_delete_requires_fresh_code_csrf_and_valid_session(api, pg, postgres_settings, sign_in):
    _, consumed = sign_in(api)
    assert request_delete(api, consumed).status_code == 401
    code = fresh_code(api, pg, postgres_settings)
    csrf = api.headers.pop("X-CSRF-Token")
    assert request_delete(api, code).status_code == 403
    api.headers["X-CSRF-Token"] = csrf
    assert request_delete(api, code).status_code == 202


def test_delete_blocks_sessions_immediately_and_token_is_status_only(
    api,
    pg,
    postgres_settings,
    sign_in,
):
    sign_in(api)
    cookie = api.cookies.get("miroven_session")
    code = fresh_code(api, pg, postgres_settings)
    result = request_delete(api, code)
    assert result.status_code == 202
    token = result.json()["deletion_token"]
    assert not api.cookies.get("miroven_session")
    api.cookies.set("miroven_session", cookie)
    for path in ("/api/v1/auth/session", "/api/v1/vault", "/api/v1/account/usage"):
        assert api.get(path, headers={"Authorization": "Bearer " + token}).status_code == 401
    assert api.get("/api/v1/account/deletion").status_code == 401
    assert api.get("/api/v1/account/deletion?token=" + token).status_code == 401
    status = api.get("/api/v1/account/deletion", headers={"Authorization": "Bearer " + token})
    assert status.json()["status"] == "pending"
    with Session(pg) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthSession)
                .where(AuthSession.revoked_at.is_(None))
            )
            == 0
        )
    assert (
        api.post(
            "/api/v1/auth/code/verify",
            json={
                "email": "one@example.com",
                "code": code,
            },
        ).status_code
        == 401
    )


def test_deletion_retries_storage_and_cascades_all_rows(
    api,
    pg,
    postgres_settings,
    sign_in,
    object_store,
):
    import hashlib

    session, _ = sign_in(api)
    user_id = UUID(session["user_id"])
    with Session(pg) as db, db.begin():
        key = f"{user_id.hex}/{uuid4().hex}"
        data = b"encrypted-account-photo"
        object_store.put(key, BytesIO(data), hashlib.sha256(data).hexdigest())
        db.add(
            BlobObject(
                user_id=user_id,
                id=uuid4(),
                entry_id=uuid4(),
                revision=1,
                storage_key=key,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status="ready",
            )
        )
    code = fresh_code(api, pg, postgres_settings)
    receipt = request_delete(api, code).json()

    class Broken:
        def delete(self, key):
            raise OSError("private storage path and credentials")

    assert process_deletion(pg, postgres_settings, Broken())
    with Session(pg) as db, db.begin():
        task = db.get(AccountDeletion, UUID(receipt["deletion_id"]))
        assert task.attempts == 1 and task.status == "pending"
        assert db.get(User, user_id).deleting_at
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1
        task.available_at = datetime.now(UTC)
    assert process_deletion(pg, postgres_settings, object_store)
    with pytest.raises(FileNotFoundError):
        object_store.get(key, 100)
    with Session(pg) as db:
        assert db.get(User, user_id) is None
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        task = db.get(AccountDeletion, UUID(receipt["deletion_id"]))
        assert task.user_id is None and task.status == "complete"
    status = api.get(
        "/api/v1/account/deletion",
        headers={
            "Authorization": "Bearer " + receipt["deletion_token"],
        },
    )
    assert status.json()["status"] == "complete"


def test_crash_after_external_delete_rolls_back_and_resumes(
    api,
    pg,
    postgres_settings,
    sign_in,
    object_store,
):
    import hashlib

    session, _ = sign_in(api)
    user_id = UUID(session["user_id"])
    key = f"{user_id.hex}/{uuid4().hex}"
    data = b"ciphertext"
    object_store.put(key, BytesIO(data), hashlib.sha256(data).hexdigest())
    with Session(pg) as db, db.begin():
        db.add(
            BlobObject(
                user_id=user_id,
                id=uuid4(),
                entry_id=uuid4(),
                revision=1,
                storage_key=key,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                status="pending",
            )
        )
    request_delete(api, fresh_code(api, pg, postgres_settings))

    class Crash:
        def delete(self, key):
            object_store.delete(key)
            raise RuntimeError("simulated process death")

    with pytest.raises(RuntimeError):
        process_deletion(pg, postgres_settings, Crash())
    with Session(pg) as db:
        assert db.get(User, user_id) is not None
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 1
    assert process_deletion(pg, postgres_settings, object_store)
    with Session(pg) as db:
        assert db.get(User, user_id) is None


def test_usage_includes_reserved_bytes_and_hides_other_accounts(api, pg, sign_in):
    result, _ = sign_in(api)
    user_id = UUID(result["user_id"])
    with Session(pg) as db, db.begin():
        other = User(email="other@example.com")
        db.add(other)
        db.flush()
        for owner, size in ((user_id, 42), (other.id, 900)):
            db.add(
                BlobObject(
                    user_id=owner,
                    id=uuid4(),
                    entry_id=uuid4(),
                    revision=1,
                    storage_key=f"{owner.hex}/{uuid4().hex}",
                    size=size,
                    sha256="a" * 64,
                    status="pending",
                )
            )
    usage = api.get("/api/v1/account/usage").json()
    assert usage["storage_bytes"] == 42
    assert usage["entries"] == 0
    assert usage["storage_limit_bytes"] == 100 * 1024 * 1024


def test_deleting_account_cannot_recreate_pending_upload(api, pg, postgres_settings, sign_in):
    sign_in(api)
    vault = {
        "expected_version": 0,
        "wrapped_vault_key": base64.b64encode(bytes(48)).decode(),
        "wrapping_nonce": base64.b64encode(bytes(12)).decode(),
        "recovery_salt": base64.b64encode(bytes(16)).decode(),
    }
    assert api.put("/api/v1/vault", json=vault).status_code == 200
    token = api.cookies.get("miroven_session")
    receipt = request_delete(api, fresh_code(api, pg, postgres_settings))
    assert receipt.status_code == 202
    api.cookies.set("miroven_session", token)
    result = api.post(
        "/api/v1/blobs",
        params={
            "blob_id": str(uuid4()),
            "entry_id": str(uuid4()),
            "revision": 1,
        },
        content=b"ciphertext",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert result.status_code == 401
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(BlobObject)) == 0
