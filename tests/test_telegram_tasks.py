"""Unit tests for app/workers/telegram_tasks.py.

Covers:
- H1: callback_query updates are not dropped early; identity extracted correctly.
- H2: dedup record is deleted when a handler raises, allowing Celery retry.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from app.workers.telegram_tasks import handle_telegram_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db() -> MagicMock:
    db = MagicMock()
    db.rollback = MagicMock()
    db.commit = MagicMock()
    return db


def _fake_wds(mock_db: MagicMock):
    """Context manager factory that yields mock_db."""
    @contextmanager
    def _cm():
        yield mock_db
    return _cm


def _base_patches(mock_db: MagicMock, mock_user_svc, mock_dedup_svc, mock_router):
    """Return the minimal patches needed to run handle_telegram_update in unit tests."""
    return [
        patch("app.workers.telegram_tasks.worker_db_session", _fake_wds(mock_db)),
        patch("app.workers.telegram_tasks._get_or_create_session", return_value=uuid.uuid4()),
        patch("app.workers.telegram_tasks.UserService", return_value=mock_user_svc),
        patch("app.workers.telegram_tasks.DedupService", return_value=mock_dedup_svc),
        patch("app.workers.telegram_tasks.needs_onboarding", return_value=False),
        # Router is an inline import from app.telegram.router — patch it at the source.
        patch("app.telegram.router.Router", return_value=mock_router),
    ]


def _make_mocks(*, dedup_new: bool = True):
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.chat_id = 777

    mock_user_svc = MagicMock()
    mock_user_svc.get_or_create.return_value = (mock_user, False)

    mock_dedup_svc = MagicMock()
    mock_dedup_svc.record_if_new.return_value = dedup_new

    mock_router = MagicMock()

    return mock_user_svc, mock_dedup_svc, mock_router, mock_user


# ---------------------------------------------------------------------------
# H1: callback_query routing
# ---------------------------------------------------------------------------


class TestCallbackQueryRouting:
    def _cq_update(
        self,
        *,
        telegram_id: int = 777,
        chat_id: int = 777,
        username: str = "user",
        data: str = "conf_u:abc123",
        message_id: int = 100,
    ) -> dict:
        return {
            "update_id": 999,
            "callback_query": {
                "id": "cbq_id",
                "from": {"id": telegram_id, "username": username},
                "message": {
                    "message_id": message_id,
                    "chat": {"id": chat_id},
                },
                "data": data,
            },
        }

    def test_callback_query_not_dropped(self):
        """callback_query update must reach the router, not be silently discarded."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            handle_telegram_update(self._cq_update())

        mock_router.route.assert_called_once()

    def test_callback_query_identity_extracted_from_cq_fields(self):
        """telegram_id and chat_id must come from callback_query.from/.message."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            handle_telegram_update(self._cq_update(telegram_id=42, chat_id=42, username="alice"))

        mock_user_svc.get_or_create.assert_called_once_with(
            telegram_id=42,
            chat_id=42,
            username="alice",
        )

    def test_regular_message_update_still_routes(self):
        """Plain message updates must continue to work correctly."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 99, "username": "bob"},
                "chat": {"id": 99},
                "text": "/inventory",
            },
        }

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            handle_telegram_update(update)

        mock_user_svc.get_or_create.assert_called_once_with(
            telegram_id=99,
            chat_id=99,
            username="bob",
        )
        mock_router.route.assert_called_once()


# ---------------------------------------------------------------------------
# H2: dedup deletion on handler failure
# ---------------------------------------------------------------------------


class TestDedupRetryOnFailure:
    def _message_update(self) -> dict:
        return {
            "update_id": 500,
            "message": {
                "message_id": 20,
                "from": {"id": 55, "username": "u"},
                "chat": {"id": 55},
                "text": "hello",
            },
        }

    def test_dedup_deleted_when_handler_raises(self):
        """On handler exception, dedup record must be deleted so retry can reprocess."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        mock_router.route.side_effect = RuntimeError("send failed")
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with pytest.raises(RuntimeError, match="send failed"):
                handle_telegram_update(self._message_update())

        mock_dedup_svc.delete_by_update_id.assert_called_once_with(500)

    def test_exception_reraised_for_celery_retry(self):
        """The original exception must propagate so Celery knows to retry."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        mock_router.route.side_effect = ValueError("boom")
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            with pytest.raises(ValueError, match="boom"):
                handle_telegram_update(self._message_update())

    def test_dedup_not_deleted_on_success(self):
        """Dedup record must NOT be deleted when the handler succeeds."""
        mock_db = _make_mock_db()
        mock_user_svc, mock_dedup_svc, mock_router, _ = _make_mocks()
        patches = _base_patches(mock_db, mock_user_svc, mock_dedup_svc, mock_router)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            handle_telegram_update(self._message_update())

        mock_dedup_svc.delete_by_update_id.assert_not_called()
