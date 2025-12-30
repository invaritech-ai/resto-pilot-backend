from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_chat_id(*, session: Session, chat_id: int) -> None:
    """
    Serialize ingestion per chat_id in Postgres.

    This prevents concurrent requests/workers from creating multiple open sessions
    or racing updates to session state for the same chat.
    """
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return

    lock_key = int(chat_id)
    min_i64 = -(2**63)
    max_i64 = 2**63 - 1
    if lock_key < min_i64 or lock_key > max_i64:
        lock_key = hash(lock_key) % max_i64
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

