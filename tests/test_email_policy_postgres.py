from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AuthSession, Job, LoginChallenge, RateWindow, User
from app.security import decrypt_job, encrypt_job
from app.worker import process_one
from tests.conftest import issued_code

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "email", ["synthetic@example.com", "synthetic@naver.com", "synthetic@yahoo.co.jp"]
)
def test_allowed_new_accounts_and_queued_locale(api, pg, postgres_settings, email):
    assert api.post("/api/v1/auth/code/request", json={"email": email.upper()}).status_code == 202
    code = issued_code(pg, postgres_settings, email)
    with Session(pg) as db:
        payload = decrypt_job(postgres_settings, db.scalar(select(Job)).encrypted_payload)
        assert (
            payload["locale"]
            == {"example.com": "en", "naver.com": "ko", "yahoo.co.jp": "ja"}[email.split("@")[1]]
        )
        assert (
            datetime.fromisoformat(payload["expires_at"])
            == db.scalar(select(LoginChallenge)).expires_at
        )
    response = api.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "email",
    [
        "new@unlisted.com",
        "new@fakegmail.com",
        "new@sub.naver.com",
        "new@naver.com.unlisted.com",
        "new+tag@example.com",
        "new.name@example.com",
        "new.name+tag@naver.com",
        "new+tag@yahoo.co.jp",
    ],
)
def test_unlisted_new_address_is_opaque_and_queues_no_mail(api, pg, email):
    accepted = api.post("/api/v1/auth/code/request", json={"email": "allowed@example.com"})
    blocked = api.post("/api/v1/auth/code/request", json={"email": email})
    assert blocked.status_code == accepted.status_code == 202
    assert blocked.json() == accepted.json()
    assert (
        api.post("/api/v1/auth/code/verify", json={"email": email, "code": "012345"}).status_code
        == 401
    )
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 1
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(RateWindow)) >= 3


def test_unlisted_requests_still_exhaust_ip_budget(api, pg, postgres_settings):
    api.app.state.settings = postgres_settings.model_copy(update={"ip_hourly_limit": 2})
    for email in ("blocked@unlisted.com", "another@unlisted.com", "allowed@example.com"):
        assert api.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_policy_tightening_rejects_outstanding_code_even_concurrently(api, pg, postgres_settings):
    email = "pending@example.com"
    api.post("/api/v1/auth/code/request", json={"email": email})
    code = issued_code(pg, postgres_settings, email)
    api.app.state.settings = postgres_settings.model_copy(
        update={"email_domains_default": "gmail.com"}
    )

    def verify(_):
        with TestClient(api.app, base_url="http://localhost:8000") as client:
            return client.post(
                "/api/v1/auth/code/verify",
                json={"email": email, "code": code},
                headers={"Origin": postgres_settings.public_origin},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(verify, range(2))) == [401, 401]
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        challenge = db.scalar(select(LoginChallenge))
        assert challenge.consumed_at is not None
        assert challenge.attempts == 1


@pytest.mark.parametrize(
    "email", ["existing@unlisted.com", "existing+tag@example.com", "existing.name@example.com"]
)
def test_restricted_existing_account_can_sign_in_and_delete(
    api, pg, postgres_settings, sign_in, email
):
    with Session(pg) as db, db.begin():
        db.add(User(email=email))
    sign_in(api, email)
    assert api.get("/api/v1/auth/session").status_code == 200
    with Session(pg) as db, db.begin():
        db.scalar(select(LoginChallenge)).created_at -= timedelta(minutes=2)
    assert api.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
    code = issued_code(pg, postgres_settings, email)
    assert api.request("DELETE", "/api/v1/account", json={"code": code}).status_code == 202
    assert api.get("/api/v1/auth/session").status_code == 401


@pytest.mark.parametrize("email", ["pending+tag@example.com", "pending.name@example.com"])
def test_legacy_alias_challenge_cannot_create_a_new_user(api, pg, postgres_settings, email):
    from datetime import UTC

    from app.security import digest

    now = datetime.now(UTC)
    code = "012345"
    with Session(pg) as db, db.begin():
        db.add(
            LoginChallenge(
                email=email,
                code_digest=digest(postgres_settings, "login-code", email + ":" + code),
                created_at=now,
                expires_at=now + timedelta(minutes=10),
            )
        )
    response = api.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(LoginChallenge)).consumed_at is not None


@pytest.mark.parametrize("email", ["existing+tag@example.com", "exist.ing@example.com"])
def test_restricted_alias_is_not_rewritten_to_an_existing_account(api, pg, email):
    with Session(pg) as db, db.begin():
        db.add(User(email="existing@example.com"))
    assert api.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_removing_domain_preserves_existing_sessions_and_login(api, pg, postgres_settings, sign_in):
    email = "existing@example.com"
    sign_in(api, email)
    api.app.state.settings = postgres_settings.model_copy(
        update={"email_domains_default": "gmail.com"}
    )
    assert api.get("/api/v1/auth/session").status_code == 200
    with Session(pg) as db, db.begin():
        db.scalar(select(LoginChallenge)).created_at -= timedelta(minutes=2)
    sign_in(api, email)


def test_retry_retains_queued_locale_after_policy_change(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "synthetic@naver.com"})
    attempts = []

    def sender(_, payload):
        attempts.append(payload["locale"])
        if len(attempts) == 1:
            raise OSError("Synthetic SMTP retry")

    assert process_one(pg, postgres_settings, sender)
    with Session(pg) as db, db.begin():
        job = db.scalar(select(Job))
        job.available_at -= timedelta(minutes=5)
        assert job.encrypted_payload is not None
    changed = postgres_settings.model_copy(update={"email_domains_kr": ""})
    assert process_one(pg, changed, sender)
    assert attempts == ["ko", "ko"]
    with Session(pg) as db:
        assert db.scalar(select(Job)).encrypted_payload is None


def test_legacy_encrypted_mail_job_remains_deliverable(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "synthetic@example.com"})
    with Session(pg) as db, db.begin():
        job = db.scalar(select(Job))
        payload = decrypt_job(postgres_settings, job.encrypted_payload)
        del payload["locale"]
        del payload["expires_at"]
        job.encrypted_payload = encrypt_job(postgres_settings, payload)
    sent = []
    assert process_one(pg, postgres_settings, lambda _, payload: sent.append(payload))
    assert len(sent) == 1 and "locale" not in sent[0]
