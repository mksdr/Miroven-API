from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AccountDeletion
from tests.test_auth_postgres import vault_body
from tests.test_sync_postgres import operation, upload

pytestmark = pytest.mark.postgres


@pytest.fixture
def admin_api(pg, postgres_settings, tmp_path):
    settings = postgres_settings.model_copy(
        update={
            "storage_backend": "local",
            "blob_directory": tmp_path / "blobs",
            "admin_emails": "admin@example.com, second-admin@example.com",
            "storage_quota_bytes": 100,
            "max_entries": 1,
            "blob_max_bytes": 50,
            "pro_storage_quota_bytes": 500,
            "pro_max_entries": 5,
            "pro_blob_max_bytes": 200,
        }
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.headers["Origin"] = settings.public_origin
        yield client


def test_admin_endpoints_enforce_authentication_allowlist_and_csrf(admin_api, sign_in):
    assert admin_api.get("/admin").status_code == 200
    assert "Content-Security-Policy" in admin_api.get("/admin").headers
    assert admin_api.get("/api/v1/admin/stats").status_code == 401

    member, _ = sign_in(admin_api, "member@example.com")
    assert admin_api.get("/api/v1/admin/stats").status_code == 403
    assert admin_api.get("/api/v1/admin/users").status_code == 403
    assert (
        admin_api.put(
            f"/api/v1/admin/users/{member['user_id']}/subscription",
            json={"plan": "pro", "status": "active"},
        ).status_code
        == 403
    )

    sign_in(admin_api, "admin@example.com")
    assert admin_api.get("/api/v1/admin/stats").status_code == 200
    csrf = admin_api.headers.pop("X-CSRF-Token")
    assert (
        admin_api.put(
            f"/api/v1/admin/users/{member['user_id']}/subscription",
            json={"plan": "pro", "status": "active"},
        ).status_code
        == 403
    )
    admin_api.headers["X-CSRF-Token"] = csrf
    assert (
        admin_api.put(
            f"/api/v1/admin/users/{member['user_id']}/subscription",
            json={"plan": "pro", "status": "active"},
        ).status_code
        == 200
    )


def test_subscription_upgrades_quotas_and_gracefully_downgrades_on_expiry(
    admin_api,
    pg,
    sign_in,
):
    member, _ = sign_in(admin_api, "writer@example.com")
    user_id = UUID(member["user_id"])
    assert admin_api.put("/api/v1/vault", json=vault_body()).status_code == 200

    # Free tier: max_entries=1, blob_max_bytes=50, storage_quota_bytes=100
    usage = admin_api.get("/api/v1/account/usage").json()
    assert usage["plan"] == "free"
    assert usage["storage_limit_bytes"] == 100
    assert usage["entry_limit"] == 1
    assert usage["blob_max_bytes"] == 50

    first_entry = operation()
    assert upload(admin_api, first_entry["entry_id"], b"x" * 60).status_code == 413
    blob1 = upload(admin_api, first_entry["entry_id"], b"x" * 40)
    assert blob1.status_code == 200
    first_entry["blob_id"] = blob1.json()["blob_id"]
    assert admin_api.post("/api/v1/sync/push", json=first_entry).status_code == 200

    # Second entry is blocked on Free tier
    second_entry = operation()
    assert admin_api.post("/api/v1/sync/push", json=second_entry).json()["code"] == "entry_limit"

    with TestClient(admin_api.app, base_url="http://localhost:8000") as console:
        console.headers["Origin"] = admin_api.app.state.settings.public_origin
        sign_in(console, "admin@example.com")

        # Admin upgrades writer@example.com to Pro
        period_end = (datetime.now(UTC) + timedelta(days=5)).isoformat()
        sub_res = console.put(
            f"/api/v1/admin/users/{user_id}/subscription",
            json={
                "plan": "pro",
                "status": "active",
                "current_period_end": period_end,
                "cancel_at_period_end": True,
                "note": "Early supporter",
            },
        )
        assert sub_res.status_code == 200
        assert sub_res.json()["plan"] == "pro"
        assert sub_res.json()["storage_limit_bytes"] == 500
        assert sub_res.json()["entry_limit"] == 5
        assert sub_res.json()["blob_max_bytes"] == 200

        # Writer now has Pro limits and can upload a 120-byte blob and push a second entry
        usage = admin_api.get("/api/v1/account/usage").json()
        assert usage["plan"] == "pro"
        assert usage["storage_limit_bytes"] == 500
        assert usage["entry_limit"] == 5
        assert usage["blob_max_bytes"] == 200
        assert usage["cancel_at_period_end"] is True

        blob2 = upload(admin_api, second_entry["entry_id"], b"y" * 120)
        assert blob2.status_code == 200
        second_entry["blob_id"] = blob2.json()["blob_id"]
        assert admin_api.post("/api/v1/sync/push", json=second_entry).status_code == 200

        # Expire the subscription via admin update
        past_end = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        expired_res = console.put(
            f"/api/v1/admin/users/{user_id}/subscription",
            json={
                "plan": "pro",
                "status": "active",
                "current_period_end": past_end,
            },
        )
        assert expired_res.status_code == 200
        assert expired_res.json()["plan"] == "free"
        assert expired_res.json()["configured_plan"] == "pro"
        assert expired_res.json()["subscription_status"] == "expired"

    # Graceful downgrade: reading, downloading, and updating/deleting existing entries still work,
    # while new entry creation and uploads over Free quota are blocked.
    usage = admin_api.get("/api/v1/account/usage").json()
    assert usage["plan"] == "free"
    assert usage["subscription_status"] == "expired"
    assert usage["storage_bytes"] == 160
    assert usage["storage_limit_bytes"] == 100
    assert admin_api.get("/api/v1/sync/changes").status_code == 200
    assert admin_api.get("/api/v1/sync/snapshot").status_code == 200
    assert len(admin_api.get(f"/api/v1/blobs/{blob2.json()['blob_id']}").content) == 120

    # Updating or deleting an existing entry succeeds even when over entry_limit
    updated_first = operation(first_entry["entry_id"], base=1, delete=True)
    assert admin_api.post("/api/v1/sync/push", json=updated_first).status_code == 200

    # Creating a new entry or uploading new bytes is blocked
    assert admin_api.post("/api/v1/sync/push", json=operation()).json()["code"] == "entry_limit"
    assert upload(admin_api, second_entry["entry_id"], b"z" * 10).json()["code"] == "storage_limit"


def test_admin_stats_user_filters_and_failed_deletion_retry(admin_api, pg, sign_in):
    u1, _ = sign_in(admin_api, "alpha@example.com")
    assert admin_api.put("/api/v1/vault", json=vault_body()).status_code == 200
    entry = operation()
    blob = upload(admin_api, entry["entry_id"], b"a" * 45).json()
    entry["blob_id"] = blob["blob_id"]
    assert admin_api.post("/api/v1/sync/push", json=entry).status_code == 200

    u2, _ = sign_in(admin_api, "beta@example.com")
    sign_in(admin_api, "admin@example.com")
    assert (
        admin_api.put(
            f"/api/v1/admin/users/{u2['user_id']}/subscription",
            json={
                "plan": "pro",
                "status": "trialing",
                "storage_quota_override_bytes": 2048,
                "max_entries_override": 50,
                "current_period_end": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
                "note": "Trial account",
            },
        ).status_code
        == 200
    )

    deletion_id = uuid4()
    now = datetime.now(UTC)
    with Session(pg) as db, db.begin():
        db.add(
            AccountDeletion(
                id=deletion_id,
                user_id=None,
                token_digest="f" * 64,
                status="failed",
                attempts=10,
                available_at=now,
                expires_at=now + timedelta(days=30),
            )
        )

    stats = admin_api.get("/api/v1/admin/stats").json()
    assert stats["admin_email"] == "admin@example.com"
    assert stats["users"]["total_users"] == 3
    assert stats["users"]["active_users"] == 3
    assert stats["users"]["vault_registered_users"] == 1
    assert stats["subscriptions"]["pro_users"] == 1
    assert stats["subscriptions"]["free_users"] == 2
    assert stats["subscriptions"]["by_status"]["trialing"] == 1
    assert stats["subscriptions"]["expiring_7d"] == 1
    assert stats["storage"]["total_blob_bytes"] == 45
    assert stats["storage"]["linked_blobs"] == 1
    assert stats["storage"]["active_entries"] == 1
    assert stats["storage"]["usage_distribution"]["low"] == 1
    assert stats["storage"]["usage_distribution"]["unused"] == 2
    assert stats["operations"]["deletions_failed"] == 1
    assert stats["operations"]["failed_deletions"][0]["deletion_id"] == str(deletion_id)

    # Filter & search users
    pro_page = admin_api.get("/api/v1/admin/users", params={"plan": "pro"}).json()
    assert pro_page["total"] == 1
    assert pro_page["items"][0]["email"] == "beta@example.com"
    assert pro_page["items"][0]["storage_limit_bytes"] == 2048
    assert pro_page["items"][0]["entry_limit"] == 50

    search_page = admin_api.get("/api/v1/admin/users", params={"q": "alpha"}).json()
    assert search_page["total"] == 1
    assert search_page["items"][0]["user_id"] == u1["user_id"]

    sorted_page = admin_api.get("/api/v1/admin/users", params={"sort": "storage_desc"}).json()
    assert sorted_page["items"][0]["email"] == "alpha@example.com"

    detail = admin_api.get(f"/api/v1/admin/users/{u2['user_id']}").json()
    assert detail["note"] == "Trial account"

    # Retry failed deletion from admin endpoint
    retry = admin_api.post(f"/api/v1/admin/deletions/{deletion_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "pending"
    assert admin_api.post(f"/api/v1/admin/deletions/{deletion_id}/retry").status_code == 409
