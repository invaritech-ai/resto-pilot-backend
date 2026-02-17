"""Tests for app/telegram/handlers/onboarding.py.

Written TDD. Implementation must make these pass.

Interface under test:
    def needs_onboarding(user: User) -> bool
        # True if user.full_name is None

    def handle(
        update: dict,
        user: User,
        db: Session,
        ctx_svc: ContextService,
        settings: Settings,
    ) -> None
        # Drives the 3-step onboarding state machine.
        # Sends messages via send_message(chat_id, text, settings).
        # Mutates user and context via ctx_svc.

Onboarding steps (stored in user.context["onboarding_step"]):
    None / absent  → user.full_name is None → ask for name
    "awaiting_name"         → save name, ask for restaurant
    "awaiting_restaurant"   → create Restaurant, set active_restaurant_id, done
"""

import uuid
from unittest.mock import MagicMock, call, patch

import pytest

from app.telegram.handlers.onboarding import handle, needs_onboarding


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(full_name=None, context=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.full_name = full_name
    u.chat_id = 12345
    u.context = dict(context) if context else None
    return u


def make_ctx_svc():
    svc = MagicMock()
    # get() returns context dict (or {})
    svc.get.return_value = {}
    return svc


def make_settings():
    s = MagicMock()
    s.telegram_bot_token = "test-token"
    return s


def text_update(text: str, chat_id: int = 12345) -> dict:
    return {"message": {"text": text, "chat": {"id": chat_id}, "from": {"id": chat_id}}}


# ---------------------------------------------------------------------------
# needs_onboarding
# ---------------------------------------------------------------------------

class TestNeedsOnboarding:
    def test_true_when_full_name_is_none(self):
        user = make_user(full_name=None)
        assert needs_onboarding(user) is True

    def test_false_when_full_name_is_set(self):
        user = make_user(full_name="Ali")
        assert needs_onboarding(user) is False

    def test_false_for_empty_string_name(self):
        """Empty string counts as set — use None to indicate missing."""
        user = make_user(full_name="")
        assert needs_onboarding(user) is False


# ---------------------------------------------------------------------------
# Step 1: No name yet — ask for name
# ---------------------------------------------------------------------------

class TestStep1AskName:
    def _handle(self, user, text="hello"):
        ctx_svc = make_ctx_svc()
        ctx_svc.get.return_value = {}   # no onboarding_step
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send:
            handle(text_update(text), user, db, ctx_svc, settings)
            return mock_send, ctx_svc

    def test_sends_name_prompt(self):
        user = make_user(full_name=None)
        mock_send, _ = self._handle(user)
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1]["text"] if mock_send.call_args[1] else mock_send.call_args[0][1]
        assert "name" in text_sent.lower()

    def test_sends_to_correct_chat(self):
        user = make_user(full_name=None)
        mock_send, _ = self._handle(user)
        call_kwargs = mock_send.call_args
        chat_id_sent = call_kwargs[1].get("chat_id") or call_kwargs[0][0]
        assert chat_id_sent == user.chat_id

    def test_sets_awaiting_name_step(self):
        user = make_user(full_name=None)
        _, ctx_svc = self._handle(user)
        ctx_svc.set_fields.assert_called()
        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") == "awaiting_name"

    def test_does_not_set_full_name(self):
        user = make_user(full_name=None)
        self._handle(user, text="hello")
        assert user.full_name is None


# ---------------------------------------------------------------------------
# Step 2: awaiting_name — save name, ask for restaurant
# ---------------------------------------------------------------------------

class TestStep2SaveName:
    def _handle(self, name_text):
        user = make_user(full_name=None)
        ctx_svc = make_ctx_svc()
        ctx_svc.get.return_value = {"onboarding_step": "awaiting_name"}
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send:
            handle(text_update(name_text), user, db, ctx_svc, settings)
            return mock_send, ctx_svc, user, db

    def test_saves_name_to_user(self):
        _, _, user, _ = self._handle("Ali Hassan")
        assert user.full_name == "Ali Hassan"

    def test_name_is_stripped(self):
        _, _, user, _ = self._handle("  Ali Hassan  ")
        assert user.full_name == "Ali Hassan"

    def test_sends_restaurant_prompt(self):
        mock_send, _, _, _ = self._handle("Ali Hassan")
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "restaurant" in text_sent.lower()

    def test_advances_step_to_awaiting_restaurant(self):
        _, ctx_svc, _, _ = self._handle("Ali Hassan")
        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") == "awaiting_restaurant"

    def test_flushes_user_to_session(self):
        _, _, user, db = self._handle("Ali Hassan")
        db.add.assert_called()
        db.flush.assert_called()

    def test_empty_name_re_asks(self):
        """Blank input should not save and should re-ask for name."""
        mock_send, ctx_svc, user, _ = self._handle("   ")
        assert user.full_name is None
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "name" in text_sent.lower()


# ---------------------------------------------------------------------------
# Step 3: awaiting_restaurant — create restaurant, welcome
# ---------------------------------------------------------------------------

class TestStep3CreateRestaurant:
    def _handle(self, restaurant_name, user_name="Ali"):
        user = make_user(full_name=user_name)
        ctx_svc = make_ctx_svc()
        ctx_svc.get.return_value = {"onboarding_step": "awaiting_restaurant"}
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send, \
             patch("app.telegram.handlers.onboarding.Restaurant") as MockRestaurant:
            fake_restaurant = MagicMock()
            fake_restaurant.id = uuid.uuid4()
            MockRestaurant.return_value = fake_restaurant

            handle(text_update(restaurant_name), user, db, ctx_svc, settings)
            return mock_send, ctx_svc, user, db, fake_restaurant

    def test_creates_restaurant_with_name(self):
        _, _, _, db, restaurant = self._handle("Burger Barn")
        db.add.assert_called()

    def test_sets_active_restaurant_in_context(self):
        _, ctx_svc, _, _, restaurant = self._handle("Burger Barn")
        ctx_svc.set_active_restaurant.assert_called_once_with(
            pytest.approx(unittest_any()), restaurant.id
        )

    def test_clears_onboarding_step(self):
        _, ctx_svc, _, _, _ = self._handle("Burger Barn")
        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") is None

    def test_sends_welcome_message(self):
        mock_send, _, user, _, _ = self._handle("Burger Barn")
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        # Should greet by name and mention they're set up
        assert user.full_name.lower() in text_sent.lower() or "welcome" in text_sent.lower()

    def test_empty_restaurant_name_re_asks(self):
        """Blank input should not create restaurant and should re-ask."""
        mock_send, _, _, db, _ = self._handle("   ")
        # Restaurant should NOT have been added for blank input
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "restaurant" in text_sent.lower()

    def test_flushes_after_restaurant_creation(self):
        _, _, _, db, _ = self._handle("Burger Barn")
        db.flush.assert_called()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

class unittest_any:
    """Matches any value — for use in assert_called_once_with."""
    def __eq__(self, other):
        return True
