from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def get_engine():
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


def get_db() -> Generator[Session, None, None]:
    """Provide a request-scoped session for dependencies."""
    engine = get_engine()
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, class_=Session
    )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
