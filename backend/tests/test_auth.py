import re
from base64 import b64encode
from collections.abc import Iterator
from secrets import token_urlsafe

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.core.config import Settings
from app.db.base import Base
from app.main import api_docs_urls, app
from auth_support import TEST_AUTH_HEADERS


@pytest.fixture
def authenticated_client() -> Iterator[TestClient]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine, autoflush=False, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, headers=TEST_AUTH_HEADERS) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_every_organization_route_rejects_missing_credentials() -> None:
    protected_routes = {
        path: operations for path, operations in app.openapi()["paths"].items()
        if path.startswith("/organizations")
    }
    assert protected_routes
    with TestClient(app) as client:
        for template, operations in protected_routes.items():
            path = re.sub(r"\{[^}]+\}", "1", template)
            for method in operations.keys() & {"get", "post", "put", "patch", "delete"}:
                verb = method.upper()
                response = client.request(verb, path, json={} if verb != "GET" else None)
                assert response.status_code == 401, f"{verb} {path}: {response.status_code} {response.text}"
                assert response.headers.get("www-authenticate") == "Basic"


def test_invalid_credentials_are_rejected_without_logging_secrets(caplog: pytest.LogCaptureFixture) -> None:
    username = token_urlsafe(18)
    password = token_urlsafe(32)
    bad_auth = {"Authorization": f"Basic {b64encode(f'{username}:{password}'.encode()).decode()}"}
    with TestClient(app, headers=bad_auth) as client:
        response = client.post("/organizations", json={"name": "Must not be created"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password."}
    assert username not in caplog.text
    assert password not in caplog.text
    assert username not in response.text
    assert password not in response.text


def test_valid_credentials_can_read_and_write_using_disposable_sqlite(authenticated_client: TestClient) -> None:
    created = authenticated_client.post("/organizations", json={"name": "Authenticated demo"})
    assert created.status_code == 201
    listed = authenticated_client.get("/organizations")
    assert listed.status_code == 200
    assert listed.json() == [created.json()]


def test_health_stays_public() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_production_api_documentation_routes_are_disabled() -> None:
    docs_app = FastAPI(**api_docs_urls("production"))
    with TestClient(docs_app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_cors_allows_configured_frontend_preflight_without_cookie_credentials() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/organizations",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers.get("access-control-allow-credentials") is None


def test_production_settings_fail_when_credentials_are_missing() -> None:
    with pytest.raises(ValidationError, match="credentials must be configured"):
        Settings(
            _env_file=None,
            app_env="production",
            demo_auth_username="",
            demo_auth_password="",
            cors_origins="https://roster.example.test",
        )


def test_production_settings_reject_weak_password_or_wildcard_cors() -> None:
    with pytest.raises(ValidationError, match="at least 16 characters"):
        Settings(
            _env_file=None,
            app_env="production",
            demo_auth_username="reviewer",
            demo_auth_password="short-password",
            cors_origins="https://roster.example.test",
        )
    with pytest.raises(ValidationError, match="explicit frontend origins"):
        Settings(
            _env_file=None,
            app_env="production",
            demo_auth_username="reviewer",
            demo_auth_password="a-strong-test-password",
            cors_origins="*",
        )
