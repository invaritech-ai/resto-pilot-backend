"""Unit tests for list pagination callback handling in buttons.py."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from app.telegram.handlers.buttons import handle


def _make_user() -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.chat_id = 100
    user.context = {}
    return user


def _make_update(data: str) -> dict:
    return {
        "callback_query": {
            "id": "cb1",
            "data": data,
            "message": {
                "message_id": 42,
                "chat": {"id": 100},
            },
        }
    }


def test_list_page_edits_message_with_rendered_page() -> None:
    user = _make_user()
    db = MagicMock()
    ctx_svc = MagicMock()
    settings = MagicMock()

    with (
        patch("app.telegram.handlers.buttons.answer_callback_query") as mock_answer,
        patch("app.telegram.handlers.buttons.edit_message_text") as mock_edit,
        patch("app.telegram.handlers.commands.build_list_page") as mock_build,
    ):
        mock_build.return_value = (
            "📦 Products\n\nPage 2/3",
            {"inline_keyboard": [[{"text": "← Prev", "callback_data": "list_p:products:0"}]]},
        )
        handle(_make_update("list_p:products:1"), user, db, ctx_svc, settings)

    mock_build.assert_called_once()
    mock_edit.assert_called_once()
    assert mock_edit.call_args.kwargs["text"].startswith("📦 Products")
    assert mock_edit.call_args.kwargs["reply_markup"]["inline_keyboard"]
    mock_answer.assert_called()


def test_list_page_invalid_page_param_answers_invalid() -> None:
    user = _make_user()
    db = MagicMock()
    ctx_svc = MagicMock()
    settings = MagicMock()

    with patch("app.telegram.handlers.buttons.answer_callback_query") as mock_answer:
        handle(_make_update("list_p:products:not-a-page"), user, db, ctx_svc, settings)

    assert "invalid" in mock_answer.call_args.kwargs["text"].lower()


def test_list_page_value_error_shows_alert() -> None:
    user = _make_user()
    db = MagicMock()
    ctx_svc = MagicMock()
    settings = MagicMock()

    with (
        patch("app.telegram.handlers.buttons.answer_callback_query") as mock_answer,
        patch("app.telegram.handlers.commands.build_list_page", side_effect=ValueError("Expired list")),
    ):
        handle(_make_update("list_p:prices:2"), user, db, ctx_svc, settings)

    assert mock_answer.call_args.kwargs["show_alert"] is True
    assert "Expired list" in mock_answer.call_args.kwargs["text"]


def test_list_page_from_stale_message_is_rejected() -> None:
    user = _make_user()
    db = MagicMock()
    ctx_svc = MagicMock()
    ctx_svc.get.return_value = {"active_list_message_id": 99}
    settings = MagicMock()

    with (
        patch("app.telegram.handlers.buttons.answer_callback_query") as mock_answer,
        patch("app.telegram.handlers.commands.build_list_page") as mock_build,
    ):
        handle(_make_update("list_p:products:1"), user, db, ctx_svc, settings)

    mock_build.assert_not_called()
    assert mock_answer.call_args.kwargs["show_alert"] is True
    assert "expired" in mock_answer.call_args.kwargs["text"].lower()
