"""Tests for app/telegram/handlers/reset.py."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.telegram.handlers.reset import handle, MAIN_MENU_TEXT


def make_user():
    u = MagicMock()
    u.id = uuid.uuid4()
    u.chat_id = 12345
    u.context = {
        "active_restaurant_id": str(uuid.uuid4()),
        "last_list_type": "suppliers",
        "last_list_offset": 20,
        "numbered_items": [str(uuid.uuid4())],
        "active_staging_id": str(uuid.uuid4()),
    }
    return u


def make_ctx_svc():
    svc = MagicMock()
    return svc


def make_settings():
    s = MagicMock()
    s.telegram_bot_token = "test-token"
    return s


class TestResetHandler:
    def test_clears_navigation_state(self):
        """Should clear navigation fields but keep active_restaurant_id."""
        user = make_user()
        ctx_svc = make_ctx_svc()
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.reset.send_message"):
            handle({}, user, db, ctx_svc, settings)

        ctx_svc.clear_navigation.assert_called_once_with(user)

    def test_commits_before_sending_message(self):
        """State must be persisted before user notification."""
        user = make_user()
        ctx_svc = make_ctx_svc()
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.reset.send_message") as mock_send:
            handle({}, user, db, ctx_svc, settings)

        # Commit is called before send_message
        db.commit.assert_called_once()

    def test_sends_main_menu(self):
        """Should send main menu text to user."""
        user = make_user()
        ctx_svc = make_ctx_svc()
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.reset.send_message") as mock_send:
            handle({}, user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["chat_id"] == user.chat_id
        assert "/list suppliers" in call_kwargs["text"]
        assert "/add supplier" in call_kwargs["text"]