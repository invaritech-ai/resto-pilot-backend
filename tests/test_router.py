"""Tests for app/telegram/router.py — 6-priority message dispatcher.

Written TDD. Implementation must make these pass.

Interface under test:
    @dataclass
    class RouteContext:
        db: Session
        user: User

    @dataclass
    class Handlers:
        reset:   Callable[[dict, RouteContext], Any]
        button:  Callable[[dict, RouteContext], Any]
        command: Callable[[dict, RouteContext], Any]
        file:    Callable[[dict, RouteContext], Any]
        pattern: Callable[[dict, RouteContext], Any]
        llm:     Callable[[dict, RouteContext], Any]

    class Router:
        def __init__(self, handlers: Handlers) -> None: ...
        def route(self, update: dict, ctx: RouteContext) -> Any: ...
"""

from unittest.mock import MagicMock

import pytest

from app.telegram.router import Handlers, RouteContext, Router


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_handlers() -> Handlers:
    return Handlers(
        reset=MagicMock(return_value="reset"),
        button=MagicMock(return_value="button"),
        command=MagicMock(return_value="command"),
        file=MagicMock(return_value="file"),
        pattern=MagicMock(return_value="pattern"),
        llm=MagicMock(return_value="llm"),
    )


def make_ctx() -> RouteContext:
    return RouteContext(db=MagicMock(), user=MagicMock())


def text_update(text: str) -> dict:
    return {"message": {"text": text, "chat": {"id": 1}, "from": {"id": 1}}}


def callback_update(data: str = "conf_u:abc123") -> dict:
    return {"callback_query": {"id": "qid", "data": data, "from": {"id": 1}}}


def file_update(kind: str = "document") -> dict:
    msg: dict = {"chat": {"id": 1}, "from": {"id": 1}}
    if kind == "document":
        msg["document"] = {"file_id": "fid", "file_unique_id": "uid"}
    elif kind == "photo":
        msg["photo"] = [{"file_id": "fid", "file_unique_id": "uid"}]
    return {"message": msg}


# ---------------------------------------------------------------------------
# Priority 1 — Global Reset
# ---------------------------------------------------------------------------

class TestPriority1Reset:
    @pytest.mark.parametrize("word", ["home", "menu", "cancel", "exit", "/start"])
    def test_reset_words_call_reset_handler(self, word: str):
        h = make_handlers()
        Router(h).route(text_update(word), make_ctx())
        h.reset.assert_called_once()

    @pytest.mark.parametrize("word", ["HOME", "Cancel", "EXIT", "Menu"])
    def test_reset_is_case_insensitive(self, word: str):
        h = make_handlers()
        Router(h).route(text_update(word), make_ctx())
        h.reset.assert_called_once()

    def test_reset_with_surrounding_whitespace(self):
        h = make_handlers()
        Router(h).route(text_update("  home  "), make_ctx())
        h.reset.assert_called_once()

    def test_reset_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(text_update("cancel"), make_ctx())
        h.button.assert_not_called()
        h.command.assert_not_called()
        h.file.assert_not_called()
        h.pattern.assert_not_called()
        h.llm.assert_not_called()

    def test_start_is_reset_not_command(self):
        """'/start' is a reset word — must NOT fall through to command handler."""
        h = make_handlers()
        Router(h).route(text_update("/start"), make_ctx())
        h.reset.assert_called_once()
        h.command.assert_not_called()

    def test_reset_handler_return_value_propagated(self):
        h = make_handlers()
        h.reset.return_value = "ack"
        result = Router(h).route(text_update("home"), make_ctx())
        assert result == "ack"


# ---------------------------------------------------------------------------
# Priority 2 — Button Callback
# ---------------------------------------------------------------------------

class TestPriority2Button:
    def test_callback_query_calls_button_handler(self):
        h = make_handlers()
        Router(h).route(callback_update("conf_u:abc"), make_ctx())
        h.button.assert_called_once()

    def test_callback_query_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(callback_update(), make_ctx())
        h.reset.assert_not_called()
        h.command.assert_not_called()
        h.file.assert_not_called()
        h.pattern.assert_not_called()
        h.llm.assert_not_called()

    def test_button_handler_return_value_propagated(self):
        h = make_handlers()
        h.button.return_value = "edited"
        result = Router(h).route(callback_update(), make_ctx())
        assert result == "edited"

    def test_callback_query_beats_file_in_same_update(self):
        """Contrived edge case: callback always wins at priority 2."""
        h = make_handlers()
        update = {
            "callback_query": {"id": "q", "data": "conf_u:abc", "from": {"id": 1}},
            "message": {"document": {"file_id": "f", "file_unique_id": "u"}},
        }
        Router(h).route(update, make_ctx())
        h.button.assert_called_once()
        h.file.assert_not_called()


# ---------------------------------------------------------------------------
# Priority 3 — Slash Command
# ---------------------------------------------------------------------------

class TestPriority3Command:
    @pytest.mark.parametrize("cmd", [
        "/list suppliers",
        "/add supplier",
        "/link supplier",
        "/uploads",
        "/switch",
    ])
    def test_slash_commands_call_command_handler(self, cmd: str):
        h = make_handlers()
        Router(h).route(text_update(cmd), make_ctx())
        h.command.assert_called_once()

    def test_command_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(text_update("/list suppliers"), make_ctx())
        h.reset.assert_not_called()
        h.button.assert_not_called()
        h.file.assert_not_called()
        h.pattern.assert_not_called()
        h.llm.assert_not_called()

    def test_start_does_not_reach_command(self):
        """/start is captured by reset (priority 1) before command."""
        h = make_handlers()
        Router(h).route(text_update("/start"), make_ctx())
        h.command.assert_not_called()


# ---------------------------------------------------------------------------
# Priority 4 — File Upload
# ---------------------------------------------------------------------------

class TestPriority4File:
    def test_document_calls_file_handler(self):
        h = make_handlers()
        Router(h).route(file_update("document"), make_ctx())
        h.file.assert_called_once()

    def test_photo_calls_file_handler(self):
        h = make_handlers()
        Router(h).route(file_update("photo"), make_ctx())
        h.file.assert_called_once()

    def test_file_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(file_update(), make_ctx())
        h.reset.assert_not_called()
        h.button.assert_not_called()
        h.command.assert_not_called()
        h.pattern.assert_not_called()
        h.llm.assert_not_called()

    def test_file_with_caption_still_routes_to_file(self):
        """A document with a text caption is still a file, not a command/llm."""
        h = make_handlers()
        update = {"message": {
            "document": {"file_id": "fid", "file_unique_id": "uid"},
            "caption": "Price list Jan 2025",
        }}
        Router(h).route(update, make_ctx())
        h.file.assert_called_once()


# ---------------------------------------------------------------------------
# Priority 5 — Structured Pattern
# ---------------------------------------------------------------------------

class TestPriority5Pattern:
    @pytest.mark.parametrize("text", [
        "#1", "#2", "#10", "#99",
    ])
    def test_hash_number_calls_pattern_handler(self, text: str):
        h = make_handlers()
        Router(h).route(text_update(text), make_ctx())
        h.pattern.assert_called_once()

    @pytest.mark.parametrize("text", [
        "5 kg", "5.5 kg", "3 ltr", "10 case", "1 each", "200 g", "500 ml", "6 pcs",
    ])
    def test_quantity_unit_calls_pattern_handler(self, text: str):
        h = make_handlers()
        Router(h).route(text_update(text), make_ctx())
        h.pattern.assert_called_once()

    def test_pattern_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(text_update("#3"), make_ctx())
        h.reset.assert_not_called()
        h.button.assert_not_called()
        h.command.assert_not_called()
        h.file.assert_not_called()
        h.llm.assert_not_called()

    @pytest.mark.parametrize("text", [
        "hello world", "add supplier", "5 please", "# 2",   # space breaks #N
        "5kgnospace",                                         # no space between qty and unit
    ])
    def test_non_patterns_do_not_call_pattern_handler(self, text: str):
        h = make_handlers()
        Router(h).route(text_update(text), make_ctx())
        h.pattern.assert_not_called()


# ---------------------------------------------------------------------------
# Priority 6 — LLM Fallback
# ---------------------------------------------------------------------------

class TestPriority6LLM:
    @pytest.mark.parametrize("text", [
        "I want to add a supplier",
        "what suppliers do I have",
        "change the price of tomatoes",
        "hello",
        "",
    ])
    def test_unmatched_text_calls_llm_handler(self, text: str):
        h = make_handlers()
        Router(h).route(text_update(text), make_ctx())
        h.llm.assert_called_once()

    def test_message_with_no_text_key_calls_llm(self):
        h = make_handlers()
        update = {"message": {"chat": {"id": 1}, "from": {"id": 1}}}
        Router(h).route(update, make_ctx())
        h.llm.assert_called_once()

    def test_llm_does_not_call_other_handlers(self):
        h = make_handlers()
        Router(h).route(text_update("free text message"), make_ctx())
        h.reset.assert_not_called()
        h.button.assert_not_called()
        h.command.assert_not_called()
        h.file.assert_not_called()
        h.pattern.assert_not_called()


# ---------------------------------------------------------------------------
# Context and update passed through
# ---------------------------------------------------------------------------

class TestContextPassThrough:
    def test_update_passed_to_handler(self):
        h = make_handlers()
        update = text_update("home")
        ctx = make_ctx()
        Router(h).route(update, ctx)
        h.reset.assert_called_once_with(update, ctx)

    def test_ctx_passed_to_handler(self):
        h = make_handlers()
        update = callback_update()
        ctx = make_ctx()
        Router(h).route(update, ctx)
        h.button.assert_called_once_with(update, ctx)
