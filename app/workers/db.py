from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from app.db.session import get_engine


def get_worker_db() -> Generator[Session, None, None]:
    engine = get_engine()
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, class_=Session
    )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def worker_db_session() -> Generator[Session, None, None]:
    yield from get_worker_db()
