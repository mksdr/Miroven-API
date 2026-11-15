import base64

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.schemas import VaultWrite


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(app, base_url="http://localhost:8000") as client:
        yield client


def test_liveness_does_not_need_database(client):
    response = client.get("/health/live")
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null"])
def test_auth_mutations_reject_foreign_or_missing_origin(client, origin):
    headers = {"Origin": origin} if origin else {}
    response = client.post("/api/v1/auth/code/request", json={}, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "origin_rejected"


def test_validation_does_not_echo_sensitive_input(client):
    response = client.post(
        "/api/v1/auth/code/verify",
        headers={"Origin": "http://localhost:8000"},
        json={"email": "private@example.com", "code": "sensitive-invalid-value"},
    )
    assert response.status_code == 422
    assert "private@example.com" not in response.text
    assert "sensitive-invalid-value" not in response.text
    assert set(response.json()) == {"code", "message", "request_id"}


def test_chunked_body_has_size_limit(client):
    response = client.post(
        "/api/v1/auth/code/request",
        headers={"Origin": "http://localhost:8000"},
        content=iter([b"x" * 32768, b"x" * 32769]),
    )
    assert response.status_code == 413


def test_production_refuses_development_secret_and_http():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")


def test_configuration_refuses_non_postgres_database():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite://")


def test_vault_rejects_noncanonical_base64():
    body = {
        "expected_version": 0,
        "wrapped_vault_key": base64.b64encode(bytes(48)).decode(),
        "wrapping_nonce": base64.b64encode(bytes(12)).decode(),
        "recovery_salt": base64.b64encode(bytes(16)).decode(),
    }
    assert VaultWrite(**body).expected_version == 0
    body["recovery_salt"] = body["recovery_salt"][:-3] + "B=="
    with pytest.raises(ValidationError):
        VaultWrite(**body)


def test_openapi_only_advertises_implemented_routes(client):
    schema = client.get("/openapi.json").json()
    assert "/api/v1/vault" in schema["paths"]
    assert "/api/v1/sync/push" in schema["paths"]
    assert "/api/v1/sync/snapshot" in schema["paths"]
    assert "/api/v1/account" in schema["paths"]
    assert "/api/v1/admin/stats" in schema["paths"]
    assert "/api/v1/admin/users" in schema["paths"]
    assert "/admin" not in schema["paths"]
    assert schema["info"]["license"]["name"] == "AGPL-3.0-only"


def test_admin_page_disabled_by_default_and_protected_by_csp_when_enabled(client):
    assert client.get("/admin").status_code == 404
    enabled_app = create_app(
        Settings(_env_file=None, environment="test", admin_emails="admin@example.com")
    )
    with TestClient(enabled_app, base_url="http://localhost:8000") as admin_client:
        response = admin_client.get("/admin")
        assert response.status_code == 200
        assert "Miroven 관리자 콘솔" in response.text
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"


def test_subscription_evaluation_and_admin_email_validation():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.models import UserSubscription
    from app.subscriptions import evaluate_subscription

    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_emails="invalid-email")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, pro_blob_max_bytes=16 * 1024 * 1024 + 1)

    settings = Settings(
        _env_file=None,
        storage_quota_bytes=100,
        max_entries=10,
        blob_max_bytes=20,
        pro_storage_quota_bytes=1000,
        pro_max_entries=100,
        pro_blob_max_bytes=200,
    )
    now = datetime.now(UTC)

    default_limits = evaluate_subscription(None, settings, now)
    assert default_limits.plan == "free"
    assert default_limits.storage_limit_bytes == 100

    active_pro = UserSubscription(
        user_id=uuid4(),
        plan="pro",
        status="active",
        current_period_end=now + timedelta(days=10),
        cancel_at_period_end=False,
        provider="manual",
    )
    pro_limits = evaluate_subscription(active_pro, settings, now)
    assert pro_limits.plan == "pro"
    assert pro_limits.storage_limit_bytes == 1000
    assert pro_limits.entry_limit == 100
    assert pro_limits.blob_max_bytes == 200

    expired_pro = UserSubscription(
        user_id=uuid4(),
        plan="pro",
        status="active",
        storage_quota_override_bytes=5000,
        current_period_end=now - timedelta(seconds=1),
        cancel_at_period_end=False,
        provider="manual",
    )
    expired_limits = evaluate_subscription(expired_pro, settings, now)
    assert expired_limits.plan == "free"
    assert expired_limits.status == "expired"
    assert expired_limits.storage_limit_bytes == 100


@pytest.mark.parametrize(
    "field",
    [
        "sync_storage_quota_bytes",
        "sync_operation_limit",
        "sync_hourly_operations",
        "sync_hourly_bytes",
    ],
)
@pytest.mark.parametrize("value", [0, -1])
def test_sync_budgets_require_positive_settings(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})
