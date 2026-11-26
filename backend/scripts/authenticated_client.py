from base64 import b64encode

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def authenticated_test_client() -> TestClient:
    """Create an in-process API client using the configured demo credentials."""
    settings = get_settings()
    if not settings.has_demo_credentials:
        raise RuntimeError("Set DEMO_AUTH_USERNAME and DEMO_AUTH_PASSWORD before using API scripts.")
    token = b64encode(
        f"{settings.demo_auth_username}:{settings.demo_auth_password.get_secret_value()}".encode("ascii")
    ).decode("ascii")
    return TestClient(app, headers={"Authorization": f"Basic {token}"})
