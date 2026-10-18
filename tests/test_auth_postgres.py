import base64
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import pytest
from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.main import create_app
from app.models import AuthSession, Job, LoginChallenge, User
from app.worker import process_one
from tests.conftest import issued_code

pytestmark = pytest.mark.postgres


def vault_body(version=0):
    return {
        "expected_version": version,
        "wrapped_vault_key": base64.b64encode(bytes(range(48))).decode(),
        "wrapping_nonce": base64.b64encode(bytes(12)).decode(),
        "recovery_salt": base64.b64encode(bytes(16)).decode(),
    }


def test_sign_in_single_use_and_session_hashes(api, pg, postgres_settings, sign_in):
    identity, code = sign_in(api)
    assert api.get("/api/v1/auth/session").json() == identity
    with Session(pg) as db:
        session = db.scalar(select(AuthSession))
        assert session.token_digest != api.cookies.get(postgres_settings.cookie_name)
        challenge = db.scalar(select(LoginChallenge))
        assert code not in challenge.code_digest
        job = db.scalar(select(Job))
        assert code not in job.encrypted_payload
        assert "one@example.com" not in job.encrypted_payload
    response = api.post("/api/v1/auth/code/verify", json={"email": identity["email"], "code": code})
    assert response.status_code == 401


def test_invalid_attempts_are_committed(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
    code = issued_code(pg, postgres_settings, "one@example.com")
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(postgres_settings.code_attempt_limit):
        assert (
            api.post(
                "/api/v1/auth/code/verify",
                json={
                    "email": "one@example.com",
                    "code": wrong,
                },
            ).status_code
            == 401
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
    with Session(pg) as db:
        assert db.scalar(select(LoginChallenge)).attempts == postgres_settings.code_attempt_limit
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_expired_code_is_rejected(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
    code = issued_code(pg, postgres_settings, "one@example.com")
    with Session(pg) as db, db.begin():
        db.scalar(select(LoginChallenge)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
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


def test_request_cooldown_does_not_queue_duplicates(api, pg):
    responses = [
        api.post("/api/v1/auth/code/request", json={"email": "one@example.com"}) for _ in range(8)
    ]
    assert len({response.text for response in responses}) == 1
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_ip_budget_applies_across_email_addresses(api, pg, postgres_settings):
    for i in range(postgres_settings.ip_hourly_limit + 2):
        assert (
            api.post(
                "/api/v1/auth/code/request", json={"email": f"user{i}@example.com"}
            ).status_code
            == 202
        )
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(Job)) == postgres_settings.ip_hourly_limit


def test_cookie_policy_and_csrf(api, sign_in):
    sign_in(api)
    api.headers.pop("X-CSRF-Token")
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 403
    assert api.post("/api/v1/auth/logout").status_code == 403


def test_expected_account_blocks_stale_tab_reads_and_writes(api, sign_in):
    first, _ = sign_in(api)
    second, _ = sign_in(api, "two@example.com")
    api.headers["X-Miroven-User-ID"] = first["user_id"]
    for response in (
        api.get("/api/v1/vault"),
        api.put("/api/v1/vault", json=vault_body()),
        api.post("/api/v1/auth/logout"),
    ):
        assert response.status_code == 409
        assert response.json()["code"] == "auth_scope_changed"
    api.headers["X-Miroven-User-ID"] = second["user_id"]
    assert api.get("/api/v1/auth/session").status_code == 200
    assert api.get("/api/v1/vault").status_code == 404


def test_vault_roundtrip_and_stale_revision(api, sign_in):
    sign_in(api)
    assert api.get("/api/v1/vault").status_code == 404
    response = api.put("/api/v1/vault", json=vault_body())
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert api.get("/api/v1/vault").json() == response.json()
    assert api.put("/api/v1/vault", json=vault_body()).status_code == 409
    assert api.put("/api/v1/vault", json=vault_body(1)).json()["version"] == 2


def test_accounts_cannot_read_or_assign_each_others_vault(api, sign_in):
    one, _code = sign_in(api)
    api.put("/api/v1/vault", json=vault_body())
    sign_in(api, "two@example.com")
    assert api.get("/api/v1/vault").status_code == 404
    body = vault_body()
    body["user_id"] = one["user_id"]
    assert api.put("/api/v1/vault", json=body).status_code == 422


def test_logout_revokes_stolen_session(api, sign_in, postgres_settings):
    sign_in(api)
    token = api.cookies.get(postgres_settings.cookie_name)
    assert api.post("/api/v1/auth/logout").status_code == 204
    api.cookies.set(postgres_settings.cookie_name, token)
    assert api.get("/api/v1/auth/session").status_code == 401


def test_concurrent_vault_creation_only_one_wins(api, sign_in, postgres_settings):
    identity, _code = sign_in(api)
    token = api.cookies.get(postgres_settings.cookie_name)

    def write():
        with TestClient(api.app, base_url="http://localhost:8000") as other:
            other.cookies.set(postgres_settings.cookie_name, token)
            return other.put(
                "/api/v1/vault",
                json=vault_body(),
                headers={
                    "Origin": postgres_settings.public_origin,
                    "X-CSRF-Token": identity["csrf_token"],
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: write(), range(2))) == [200, 409]


def test_mail_retry_survives_new_worker_and_scrubs_payload(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})

    def unavailable(settings, payload):
        raise OSError("Do not expose email or password in logs")

    assert process_one(pg, postgres_settings, unavailable)
    with Session(pg) as db, db.begin():
        job = db.scalar(select(Job))
        assert job.attempts == 1
        assert job.status == "pending"
        job.available_at = datetime.now(UTC) - timedelta(seconds=1)
    delivered = []
    assert process_one(pg, postgres_settings, lambda settings, payload: delivered.append(payload))
    assert delivered[0]["email"] == "one@example.com"
    with Session(pg) as db:
        job = db.scalar(select(Job))
        assert job.status == "complete"
        assert job.encrypted_payload is None


def test_readiness_requires_migrated_database(api):
    assert api.get("/health/ready").status_code == 200


def test_expired_session_is_rejected(api, pg, sign_in):
    sign_in(api)
    with Session(pg) as db, db.begin():
        db.scalar(select(AuthSession)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert api.get("/api/v1/auth/session").status_code == 401


def test_logout_all_revokes_every_device(api, pg, sign_in, postgres_settings):
    identity, _code = sign_in(api)
    first_token = api.cookies.get(postgres_settings.cookie_name)
    with Session(pg) as db, db.begin():
        db.scalar(select(LoginChallenge)).created_at -= timedelta(minutes=2)
    sign_in(api)
    assert api.cookies.get(postgres_settings.cookie_name) != first_token
    assert api.post("/api/v1/auth/logout-all").status_code == 204
    api.cookies.set(postgres_settings.cookie_name, first_token)
    assert api.get("/api/v1/auth/session").status_code == 401
    with Session(pg) as db:
        assert all(s.revoked_at is not None for s in db.scalars(select(AuthSession)))


def test_concurrent_verification_consumes_code_once(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
    code = issued_code(pg, postgres_settings, "one@example.com")

    def verify():
        with TestClient(api.app, base_url="http://localhost:8000") as other:
            return other.post(
                "/api/v1/auth/code/verify",
                json={
                    "email": "one@example.com",
                    "code": code,
                },
                headers={"Origin": postgres_settings.public_origin},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: verify(), range(2))) == [200, 401]


def test_worker_does_not_send_expired_code(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
    with Session(pg) as db, db.begin():
        db.scalar(select(Job)).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    sent = []
    assert process_one(pg, postgres_settings, lambda *_: sent.append(True))
    assert not sent


def test_corrupt_job_does_not_block_queue(api, pg, postgres_settings):
    api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
    with Session(pg) as db, db.begin():
        db.scalar(select(Job)).encrypted_payload = "invalid-ciphertext"
    assert process_one(pg, postgres_settings)
    with Session(pg) as db:
        job = db.scalar(select(Job))
        assert job.status == "failed"
        assert job.encrypted_payload is None


def test_real_smtp_delivery_code_can_sign_in(api, pg, postgres_settings):
    class Inbox:
        messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope.original_content)
            return "250 Message accepted"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    inbox = Inbox()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
        # Compose supplies SMTP settings for Mailpit; this test owns a loopback server.
        settings = postgres_settings.model_copy(
            update={
                "smtp_host": "127.0.0.1",
                "smtp_port": port,
                "smtp_starttls": False,
                "smtp_username": None,
                "smtp_password": None,
            }
        )
        assert process_one(pg, settings)
    finally:
        controller.stop()
    assert len(inbox.messages) == 1
    mail = BytesParser(policy=policy.default).parsebytes(inbox.messages[0])
    code = re.search(r"\b[0-9]{6}\b", mail.get_body().get_content()).group()
    assert mail["To"] == "one@example.com"
    assert (
        api.post(
            "/api/v1/auth/code/verify",
            json={
                "email": "one@example.com",
                "code": code,
            },
        ).status_code
        == 200
    )


def test_https_signin_cookie_is_secure(pg, postgres_settings):
    settings = postgres_settings.model_copy(update={"public_origin": "https://localhost:8000"})
    with TestClient(create_app(settings), base_url=settings.public_origin) as client:
        client.headers["Origin"] = settings.public_origin
        client.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
        code = issued_code(pg, settings, "one@example.com")
        response = client.post(
            "/api/v1/auth/code/verify",
            json={
                "email": "one@example.com",
                "code": code,
            },
        )
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_email_hourly_budget_survives_resend_cooldown(api, pg, postgres_settings):
    for _ in range(postgres_settings.email_hourly_limit + 2):
        api.post("/api/v1/auth/code/request", json={"email": "one@example.com"})
        with Session(pg) as db, db.begin():
            for challenge in db.scalars(select(LoginChallenge)):
                challenge.created_at -= timedelta(minutes=2)
    with Session(pg) as db:
        assert (
            db.scalar(select(func.count()).select_from(Job)) == postgres_settings.email_hourly_limit
        )
