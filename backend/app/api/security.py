import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings

basic_auth = HTTPBasic()


def require_demo_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(basic_auth)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Require the configured shared demo credentials without logging them."""
    if not settings.has_demo_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured.",
        )

    username_matches = secrets.compare_digest(credentials.username, settings.demo_auth_username)
    password_matches = secrets.compare_digest(
        credentials.password,
        settings.demo_auth_password.get_secret_value(),
    )
    if not (username_matches and password_matches):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Basic"},
        )
