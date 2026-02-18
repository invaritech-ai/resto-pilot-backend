"""Tests for app/services/dedup_service.py.

Tests cover:
- Duplicate detection via update_id
- Race condition handling with IntegrityError
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.dedup_service import DedupService


class TestDedupService:
    def test_is_duplicate_returns_true_when_exists(self):
        """is_duplicate returns True when update_id already in DB."""
        session = MagicMock()
        session.scalar.return_value = uuid.uuid4()  # Existing record ID

        svc = DedupService(session)
        result = svc.is_duplicate(12345)

        assert result is True
        session.scalar.assert_called_once()

    def test_is_duplicate_returns_false_when_not_exists(self):
        """is_duplicate returns False when update_id not in DB."""
        session = MagicMock()
        session.scalar.return_value = None  # No existing record

        svc = DedupService(session)
        result = svc.is_duplicate(12345)

        assert result is False

    def test_record_returns_message_on_success(self):
        """record returns the TelegramMessages object on success."""
        session = MagicMock()
        session.flush.return_value = None

        svc = DedupService(session)
        result = svc.record(
            update_id=12345,
            session_id=uuid.uuid4(),
            chat_id=123456,
            user_id=uuid.uuid4(),
            telegram_id=123456,
            message_id=1,
            text="hello",
        )

        assert result is not None
        session.add.assert_called_once()
        session.flush.assert_called_once()

    def test_record_returns_none_on_integrity_error(self):
        """record returns None when IntegrityError (duplicate) occurs."""
        session = MagicMock()
        # Create an IntegrityError that looks like an update_id duplicate
        exc = IntegrityError(
            "statement", {}, Exception("UNIQUE constraint failed: update_id")
        )
        session.flush.side_effect = exc

        svc = DedupService(session)
        result = svc.record(
            update_id=12345,
            session_id=uuid.uuid4(),
            chat_id=123456,
            user_id=uuid.uuid4(),
            telegram_id=123456,
            message_id=1,
        )

        assert result is None
        session.rollback.assert_called_once()

    def test_record_if_new_returns_true_on_success(self):
        """record_if_new returns True when record is created."""
        session = MagicMock()
        session.flush.return_value = None

        svc = DedupService(session)
        result = svc.record_if_new(
            update_id=12345,
            session_id=uuid.uuid4(),
            chat_id=123456,
            user_id=uuid.uuid4(),
            telegram_id=123456,
            message_id=1,
        )

        assert result is True
        session.add.assert_called_once()

    def test_record_if_new_returns_false_on_duplicate(self):
        """record_if_new returns False when duplicate detected."""
        session = MagicMock()
        # Create an IntegrityError that looks like an update_id duplicate
        exc = IntegrityError(
            "statement", {}, Exception("UNIQUE constraint failed: update_id")
        )
        session.flush.side_effect = exc

        svc = DedupService(session)
        result = svc.record_if_new(
            update_id=12345,
            session_id=uuid.uuid4(),
            chat_id=123456,
            user_id=uuid.uuid4(),
            telegram_id=123456,
            message_id=1,
        )

        assert result is False
        session.rollback.assert_called_once()
