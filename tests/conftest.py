"""Shared pytest fixtures.

pg_session — real PostgreSQL (Neon) session for integration tests.

The session uses SAVEPOINT mode so that session.commit() inside the code
under test releases the savepoint but does NOT commit the outer connection
transaction. The outer transaction is rolled back at fixture teardown,
leaving the database clean after each test.

pg_trgm, FOR UPDATE, CHECK constraints, and JSONB all work because this
is a real PostgreSQL connection — no workarounds needed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401 — registers all models with Base.metadata


@pytest.fixture()
def pg_session() -> Session:
    """Yield a real PostgreSQL Session that rolls back after each test.

    Uses join_transaction_mode="create_savepoint" so that every
    session.commit() inside the handler releases a SAVEPOINT (not the
    outer transaction). The outer rollback cleans up all test data.
    """
    from app.core.config import get_settings

    settings = get_settings()
    engine = create_engine(settings.database_url)
    conn = engine.connect()
    outer_tx = conn.begin()
    session = Session(conn, join_transaction_mode="create_savepoint")

    try:
        yield session
    finally:
        session.close()
        outer_tx.rollback()
        conn.close()
        engine.dispose()
