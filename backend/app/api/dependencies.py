from collections.abc import Generator

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield one short-lived database session per request."""
    with SessionLocal() as session:
        yield session
