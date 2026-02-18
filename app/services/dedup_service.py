"""Update deduplication service.

Prevents duplicate processing of Telegram updates under concurrent/replayed scenarios.
Uses the unique constraint on telegram_messages.update_id as a dedup mechanism.

Usage:
    dedup_svc = DedupService(db)
    if dedup_svc.is_duplicate(update_id):
        return  # skip processing
    dedup_svc.record(update_id, session_id, chat_id, user_id, telegram_id, message_id)

IMPORTANT: This service only catches duplicate update_id violations.
All other IntegrityErrors are re-raised to avoid masking real schema bugs.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.telegram_messages import TelegramMessages


# PostgreSQL error code for unique constraint violation
PG_UNIQUE_VIOLATION = "23505"


class DedupService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_duplicate(self, update_id: int) -> bool:
        """Check if this update_id has already been processed.

        Uses a fast existence check without loading the full row.
        """
        existing = self.session.scalar(
            select(TelegramMessages.id).where(TelegramMessages.update_id == update_id)
        )
        return existing is not None

    def _is_update_id_duplicate_error(self, exc: IntegrityError) -> bool:
        """Check if IntegrityError is specifically a duplicate update_id violation.

        Re-raises if it's a different constraint violation to avoid masking bugs.
        """
        # Check PostgreSQL error code
        if hasattr(exc, "orig") and exc.orig is not None:
            pgcode = getattr(exc.orig, "pgcode", None)
            if pgcode == PG_UNIQUE_VIOLATION:
                # Check if it's the update_id constraint specifically
                diag = getattr(exc.orig, "diag", None)
                if diag and hasattr(diag, "constraint_name"):
                    constraint_name = diag.constraint_name
                    if constraint_name and "update_id" in constraint_name.lower():
                        return True
                # It's a unique violation but not update_id - re-raise
                return False
        # For SQLite or other DBs, check the error message
        msg = str(exc).lower()
        if "update_id" in msg or "unique" in msg:
            return True
        return False

    def record(
        self,
        update_id: int,
        session_id: uuid.UUID,
        chat_id: int,
        user_id: uuid.UUID,
        telegram_id: int,
        message_id: int,
        text: str | None = None,
    ) -> TelegramMessages | None:
        """Record an update_id as processed.

        Returns the created record on success, None if already exists (duplicate).
        Re-raises IntegrityError for non-update_id violations to avoid masking bugs.
        """
        msg = TelegramMessages(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            telegram_id=telegram_id,
            message_id=message_id,
            update_id=update_id,
            received_at=dt.datetime.now(dt.timezone.utc),
            text=text,
        )
        self.session.add(msg)
        try:
            self.session.flush()
            return msg
        except IntegrityError as e:
            self.session.rollback()
            if self._is_update_id_duplicate_error(e):
                # Duplicate update_id - another worker beat us to it
                return None
            # Different integrity error - re-raise to surface bugs
            raise

    def record_if_new(
        self,
        update_id: int,
        session_id: uuid.UUID,
        chat_id: int,
        user_id: uuid.UUID,
        telegram_id: int,
        message_id: int,
        text: str | None = None,
    ) -> bool:
        """Record an update_id only if not already processed.

        Returns True if this is a new update (recorded successfully),
        False if it's a duplicate (already exists).
        Re-raises IntegrityError for non-update_id violations to avoid masking bugs.

        This is the recommended method for dedup at the start of processing.
        """
        # Try to insert first (optimistic approach)
        # This avoids the race between check and insert
        msg = TelegramMessages(
            session_id=session_id,
            chat_id=chat_id,
            user_id=user_id,
            telegram_id=telegram_id,
            message_id=message_id,
            update_id=update_id,
            received_at=dt.datetime.now(dt.timezone.utc),
            text=text,
        )
        self.session.add(msg)
        try:
            self.session.flush()
            return True
        except IntegrityError as e:
            self.session.rollback()
            if self._is_update_id_duplicate_error(e):
                # Duplicate update_id - another worker already processed this
                return False
            # Different integrity error - re-raise to surface bugs
            raise
