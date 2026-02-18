"""Unit tests for app/telegram/handlers/files.py.

All external calls (StagingService, httpx, send_message) are mocked.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.telegram.handlers.files import handle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(chat_id: int = 123456) -> MagicMock:
    u = MagicMock()
    u.id = uuid.uuid4()
    u.chat_id = chat_id
    return u


def _make_settings(bot_token: str = "faketoken") -> MagicMock:
    s = MagicMock()
    s.telegram_bot_token = bot_token
    return s


def _make_staging(staging_id: uuid.UUID | None = None) -> MagicMock:
    s = MagicMock()
    s.id = staging_id or uuid.uuid4()
    return s


def _make_ctx_svc(restaurant_id: uuid.UUID | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.get_active_restaurant_id.return_value = restaurant_id or uuid.uuid4()
    return ctx


def _make_db() -> MagicMock:
    return MagicMock()


def _doc_update(
    file_id: str = "file_abc",
    file_unique_id: str = "unique_abc",
    mime: str = "application/pdf",
    chat_id: int = 123456,
) -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "document": {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "mime_type": mime,
            },
        }
    }


def _photo_update(
    file_id: str = "photo_large",
    file_unique_id: str = "unique_photo",
    chat_id: int = 123456,
) -> dict:
    return {
        "message": {
            "chat": {"id": chat_id},
            "photo": [
                {"file_id": "photo_small", "file_unique_id": "u_small", "file_size": 100},
                {"file_id": file_id, "file_unique_id": file_unique_id, "file_size": 9999},
                {"file_id": "photo_mid", "file_unique_id": "u_mid", "file_size": 1000},
            ],
        }
    }


# ---------------------------------------------------------------------------
# Document upload
# ---------------------------------------------------------------------------


class TestHandleDocument:
    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_creates_staging_for_document(self, mock_post, MockStagingService):
        staging = _make_staging()
        svc_instance = MockStagingService.return_value
        svc_instance.create.return_value = staging

        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        svc_instance.create.assert_called_once()
        call_kwargs = svc_instance.create.call_args[1]
        assert call_kwargs["file_id"] == "file_abc"
        assert call_kwargs["file_unique_id"] == "unique_abc"
        assert call_kwargs["mime"] == "application/pdf"
        assert call_kwargs["uploaded_by"] == user.id

    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_sets_active_staging_in_context(self, mock_post, MockStagingService):
        staging = _make_staging()
        MockStagingService.return_value.create.return_value = staging
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        ctx_svc.set_active_staging.assert_called_once_with(user, staging.id)

    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_commits_after_staging_created(self, mock_post, MockStagingService):
        staging = _make_staging()
        MockStagingService.return_value.create.return_value = staging
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        db.commit.assert_called_once()

    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_sends_doc_type_keyboard(self, mock_post, MockStagingService):
        staging = _make_staging()
        MockStagingService.return_value.create.return_value = staging
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        # httpx.post should be called to send the keyboard message
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        assert payload["chat_id"] == user.chat_id
        assert "📎" in payload["text"]
        assert "reply_markup" in payload


# ---------------------------------------------------------------------------
# Photo upload
# ---------------------------------------------------------------------------


class TestHandlePhoto:
    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_picks_largest_photo(self, mock_post, MockStagingService):
        staging = _make_staging()
        svc_instance = MockStagingService.return_value
        svc_instance.create.return_value = staging
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_photo_update(file_id="photo_large", file_unique_id="u_large"), user, db, ctx_svc, settings)

        call_kwargs = svc_instance.create.call_args[1]
        assert call_kwargs["file_id"] == "photo_large"
        assert call_kwargs["mime"] == "image/jpeg"

    @patch("app.telegram.handlers.files.StagingService")
    @patch("app.telegram.handlers.files.httpx.post")
    def test_photo_mime_is_jpeg(self, mock_post, MockStagingService):
        staging = _make_staging()
        MockStagingService.return_value.create.return_value = staging
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 1}})
        mock_post.return_value.raise_for_status = MagicMock()

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        handle(_photo_update(), user, db, ctx_svc, settings)

        call_kwargs = MockStagingService.return_value.create.call_args[1]
        assert call_kwargs["mime"] == "image/jpeg"


# ---------------------------------------------------------------------------
# No active restaurant
# ---------------------------------------------------------------------------


class TestNoActiveRestaurant:
    @patch("app.telegram.handlers.files.send_message")
    @patch("app.telegram.handlers.files.StagingService")
    def test_sends_error_if_no_restaurant(self, MockStagingService, mock_send):
        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc(restaurant_id=None)
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        # Should NOT create a staging record
        MockStagingService.return_value.create.assert_not_called()

        # Should send an error message
        mock_send.assert_called_once()
        text = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "restaurant" in text.lower() or "select" in text.lower()

    @patch("app.telegram.handlers.files.send_message")
    @patch("app.telegram.handlers.files.StagingService")
    def test_no_commit_if_no_restaurant(self, MockStagingService, mock_send):
        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc(restaurant_id=None)
        settings = _make_settings()

        handle(_doc_update(), user, db, ctx_svc, settings)

        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# Unknown file / no recognisable attachment
# ---------------------------------------------------------------------------


class TestUnknownFile:
    @patch("app.telegram.handlers.files.send_message")
    def test_sends_error_for_unknown_attachment(self, mock_send):
        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        # Update with neither document nor photo
        update = {"message": {"text": "hello"}}
        handle(update, user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        text = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "document" in text.lower() or "file" in text.lower() or "image" in text.lower()

    @patch("app.telegram.handlers.files.send_message")
    def test_no_staging_created_for_unknown(self, mock_send):
        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings()

        with patch("app.telegram.handlers.files.StagingService") as MockSvc:
            handle({"message": {"text": "hello"}}, user, db, ctx_svc, settings)
            MockSvc.return_value.create.assert_not_called()


# ---------------------------------------------------------------------------
# No bot token fallback
# ---------------------------------------------------------------------------


class TestNoBotToken:
    @patch("app.telegram.handlers.files.send_message")
    @patch("app.telegram.handlers.files.StagingService")
    def test_falls_back_to_plain_send_message_when_no_token(self, MockStagingService, mock_send):
        staging = _make_staging()
        MockStagingService.return_value.create.return_value = staging

        user = _make_user()
        db = _make_db()
        ctx_svc = _make_ctx_svc()
        settings = _make_settings(bot_token="")  # No token

        handle(_doc_update(), user, db, ctx_svc, settings)

        # With no bot token, falls back to send_message
        mock_send.assert_called()
