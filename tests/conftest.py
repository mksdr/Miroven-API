import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import build_engine
from app.main import create_app
from app.models import Base, Job
from app.security import decrypt_job
from app.storage import LocalStorage, S3Storage


@pytest.fixture(scope="session")
def postgres_settings():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Refusing destructive cleanup: test database name must end in _test")
    return Settings(
        _env_file=None, environment="test", database_url=url, email_domains_default="example.com"
    )


@pytest.fixture(scope="session")
def postgres_engine(postgres_settings):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["settings"] = postgres_settings
    command.upgrade(config, "head")
    engine = build_engine(postgres_settings)
    yield engine
    engine.dispose()


@pytest.fixture
def pg(postgres_engine):
    names = ", ".join('"' + name + '"' for name in Base.metadata.tables)
    with postgres_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} CASCADE"))
    yield postgres_engine


@pytest.fixture
def api(pg, postgres_settings, tmp_path):
    settings = postgres_settings.model_copy(
        update={
            "storage_backend": "local",
            "blob_directory": tmp_path / "blobs",
        }
    )
    app = create_app(settings)
    with TestClient(app, base_url="http://localhost:8000") as client:
        client.headers["Origin"] = postgres_settings.public_origin
        yield client


def issued_code(engine, settings, email):
    with Session(engine) as db:
        for job in db.query(Job).order_by(Job.available_at.desc()).all():
            if job.encrypted_payload:
                payload = decrypt_job(settings, job.encrypted_payload)
                if payload["email"] == email:
                    return payload["code"]
    raise AssertionError("No login email queued")


@pytest.fixture
def sign_in(pg, postgres_settings):
    def sign_in(client, email="one@example.com"):
        assert client.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
        code = issued_code(pg, postgres_settings, email)
        response = client.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response.json(), code

    return sign_in


@pytest.fixture(params=["local", "s3"])
def object_store(request, tmp_path):
    if request.param == "local":
        yield LocalStorage(tmp_path / "objects")
    else:
        with mock_aws():
            settings = Settings(
                _env_file=None,
                storage_backend="s3",
                s3_bucket="miroven-test",
                s3_endpoint_url=None,
                s3_access_key=SecretStr("testing"),
                s3_secret_key=SecretStr("testing"),
            )
            storage = S3Storage(settings)
            storage.client.create_bucket(Bucket="miroven-test")
            yield storage
