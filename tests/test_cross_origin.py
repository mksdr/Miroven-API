import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from tests.conftest import issued_code

WEB = "https://miroven-app.mksdr.bid"
API = "https://miroven-api.mksdr.bid"


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, public_origin=WEB, api_origin=API))

    @app.get("/test-failure")
    def failure():
        raise RuntimeError("private details")

    with TestClient(app, base_url=API, raise_server_exceptions=False) as client:
        yield client


def test_credentialed_preflight_for_configured_app_only(client):
    headers = {
        "Origin": WEB,
        "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "content-type,x-csrf-token,x-miroven-user-id",
    }
    response = client.options("/api/v1/account", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == WEB
    assert response.headers["access-control-allow-credentials"] == "true"
    for origin in ("https://miroven.mksdr.bid", "https://preview.pages.dev", "null"):
        response = client.options("/api/v1/account", headers={**headers, "Origin": origin})
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers


def test_api_host_is_independent_and_errors_are_readable(client):
    for path, status in (
        ("/health/live", 200),
        ("/api/v1/auth/session", 401),
        ("/test-failure", 500),
    ):
        response = client.get(path, headers={"Origin": WEB})
        assert response.status_code == status
        assert response.headers["access-control-allow-origin"] == WEB
        assert response.headers["x-request-id"]
        assert response.headers["cache-control"] == "no-store"
        assert "private details" not in response.text
    assert client.get("/health/live", headers={"Host": "miroven-app.mksdr.bid"}).status_code == 400


def test_mutation_still_checks_origin_and_csrf(client):
    response = client.post("/api/v1/auth/code/verify", headers={"Origin": WEB}, json={})
    assert response.status_code == 422
    assert response.headers["access-control-allow-origin"] == WEB
    for origin in ("https://miroven.mksdr.bid", "https://evil.example", "null", ""):
        response = client.post("/api/v1/auth/logout", headers={"Origin": origin})
        assert response.status_code == 403
        assert response.json()["code"] == "origin_rejected"
        assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.example/path",
        "https://u:p@api.example",
        "https://api.example?secret=x",
        "ftp://api.example",
    ],
)
def test_invalid_api_origin_is_rejected(origin):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_origin=origin)


def test_split_host_session_cookie_csrf_and_logout(pg, postgres_settings):
    settings = postgres_settings.model_copy(update={"public_origin": WEB, "api_origin": API})
    app = create_app(settings)
    email = "cross-origin@example.com"
    with TestClient(app, base_url=API, headers={"Origin": WEB}) as client:
        assert client.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
        code = issued_code(pg, settings, email)
        response = client.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
        assert "domain=" not in cookie.lower()
        assert client.get("/api/v1/auth/session").status_code == 200
        assert client.post("/api/v1/auth/logout").status_code == 403
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401
