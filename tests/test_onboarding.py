"""Tests for app/telegram/handlers/onboarding.py.

Interface under test:
    def needs_onboarding(user: User) -> bool

    def handle(
        update: dict,
        user: User,
        db: Session,
        ctx_svc: ContextService,
        settings: Settings,
    ) -> None
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


def make_ctx_svc(context=None):
    svc = MagicMock()
    svc.get.return_value = dict(context) if context else {}
    return svc


def make_settings():
    s = MagicMock()
    s.telegram_bot_token = "test-token"
    return s


def text_update(text: str, chat_id: int = 12345) -> dict:
    return {"message": {"text": text, "chat": {"id": chat_id}, "from": {"id": chat_id}}}


_ACTIVE_RESTAURANT_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# needs_onboarding
# ---------------------------------------------------------------------------

class TestNeedsOnboarding:
    def test_true_when_full_name_is_none(self):
        user = make_user(full_name=None)
        assert needs_onboarding(user) is True

    def test_false_when_fully_onboarded(self):
        """Name set + no pending step + active_restaurant_id = done."""
        user = make_user(full_name="Ali", context={"active_restaurant_id": _ACTIVE_RESTAURANT_ID})
        assert needs_onboarding(user) is False

    def test_false_for_empty_string_name_when_restaurant_active(self):
        """Empty string full_name still counts as set (not None)."""
        user = make_user(full_name="", context={"active_restaurant_id": _ACTIVE_RESTAURANT_ID})
        assert needs_onboarding(user) is False

    def test_true_when_step_still_in_context(self):
        """Name saved but onboarding_step still set means step 3 is pending."""
        user = make_user(full_name="Ali", context={"onboarding_step": "awaiting_restaurant"})
        assert needs_onboarding(user) is True

    def test_true_when_name_set_but_no_restaurant_in_context(self):
        """Webapp auth sets full_name but doesn't set active_restaurant_id."""
        user = make_user(full_name="Ali", context={})
        assert needs_onboarding(user) is True

    def test_true_when_context_is_none(self):
        """No context at all → no active_restaurant_id → onboarding needed."""
        user = make_user(full_name="Ali", context=None)
        assert needs_onboarding(user) is True

    def test_true_when_active_restaurant_id_is_invalid_uuid(self):
        """Corrupted context with invalid UUID should require onboarding."""
        user = make_user(full_name="Ali", context={"active_restaurant_id": "not-a-uuid"})
        assert needs_onboarding(user) is True


# ---------------------------------------------------------------------------
# Step 1: No name yet — ask for name
# ---------------------------------------------------------------------------

class TestStep1AskName:
    def _handle(self, user, text="hello"):
        ctx_svc = make_ctx_svc()  # no onboarding_step
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
# Webapp bypass: full_name set (e.g. via webapp auth) but no restaurant yet
# ---------------------------------------------------------------------------

class TestWebappBypass:
    def _handle(self, text="hello"):
        # full_name set, no step, no active_restaurant_id
        user = make_user(full_name="Ali", context={})
        ctx_svc = make_ctx_svc(context={})
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send:
            handle(text_update(text), user, db, ctx_svc, settings)
            return mock_send, ctx_svc, user

    def test_asks_for_restaurant(self):
        mock_send, _, _ = self._handle()
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "restaurant" in text_sent.lower()

    def test_sets_awaiting_restaurant_step(self):
        _, ctx_svc, _ = self._handle()
        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") == "awaiting_restaurant"

    def test_greets_by_name(self):
        mock_send, _, user = self._handle()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert user.full_name in text_sent


# ---------------------------------------------------------------------------
# Step 2: awaiting_name — save name, ask for restaurant
# ---------------------------------------------------------------------------

class TestStep2SaveName:
    def _handle(self, name_text):
        user = make_user(full_name=None)
        ctx_svc = make_ctx_svc(context={"onboarding_step": "awaiting_name"})
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

    def test_commits_before_sending_message(self):
        """Name must be persisted before the user is notified."""
        _, _, user, db = self._handle("Ali Hassan")
        db.add.assert_called()
        db.commit.assert_called()

    def test_empty_name_re_asks(self):
        mock_send, ctx_svc, user, _ = self._handle("   ")
        assert user.full_name is None
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "name" in text_sent.lower()

    def test_reset_word_re_asks(self):
        """Reset words like /start should not be saved as names."""
        mock_send, _, user, _ = self._handle("/start")
        assert user.full_name is None
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "name" in text_sent.lower()

    def test_slash_command_re_asks(self):
        """Slash commands should not be saved as names."""
        mock_send, _, user, _ = self._handle("/help")
        assert user.full_name is None
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "name" in text_sent.lower()

    def test_cancel_word_re_asks(self):
        mock_send, _, user, _ = self._handle("cancel")
        assert user.full_name is None


# ---------------------------------------------------------------------------
# Step 3: awaiting_restaurant — create restaurant, welcome
# ---------------------------------------------------------------------------

class TestStep3CreateRestaurant:
    def _handle(self, restaurant_name, user_name="Ali"):
        user = make_user(full_name=user_name)
        ctx_svc = make_ctx_svc(context={"onboarding_step": "awaiting_restaurant"})
        settings = make_settings()
        db = MagicMock()

        fake_restaurant = MagicMock()
        fake_restaurant.id = uuid.uuid4()
        fake_restaurant.name = restaurant_name

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send, \
             patch("app.telegram.handlers.onboarding.RestaurantService") as MockRestaurantService, \
             patch("app.telegram.handlers.onboarding._get_existing_restaurant_for_user") as mock_get_existing:

            mock_svc = MagicMock()
            MockRestaurantService.return_value = mock_svc
            mock_svc.list_for_user.return_value = []
            mock_svc.create_restaurant.return_value = fake_restaurant
            mock_get_existing.return_value = None  # No existing restaurant

            handle(text_update(restaurant_name), user, db, ctx_svc, settings)
            return mock_send, ctx_svc, user, db, fake_restaurant, mock_svc

    def test_uses_restaurant_service(self):
        """Must use RestaurantService (creates owner membership), not Restaurant directly."""
        _, _, _, _, _, mock_svc = self._handle("Burger Barn")
        mock_svc.create_restaurant.assert_called_once()

    def test_creates_with_correct_owner_and_name(self):
        _, _, user, _, _, mock_svc = self._handle("Burger Barn")
        kwargs = mock_svc.create_restaurant.call_args[1]
        assert kwargs["name"] == "Burger Barn"
        assert kwargs["owner_user_id"] == user.id

    def test_sets_active_restaurant_in_context(self):
        _, ctx_svc, _, _, restaurant, _ = self._handle("Burger Barn")
        ctx_svc.set_active_restaurant.assert_called_once_with(
            AnyArg(), restaurant.id
        )

    def test_clears_onboarding_step(self):
        _, ctx_svc, _, _, _, _ = self._handle("Burger Barn")
        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") is None

    def test_sends_welcome_message(self):
        mock_send, _, user, _, _, _ = self._handle("Burger Barn")
        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert user.full_name.lower() in text_sent.lower() or "welcome" in text_sent.lower()

    def test_empty_restaurant_name_re_asks(self):
        mock_send, _, _, _, _, mock_svc = self._handle("   ")
        mock_svc.create_restaurant.assert_not_called()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "restaurant" in text_sent.lower()

    def test_reset_word_not_saved_as_restaurant(self):
        _, _, _, _, _, mock_svc = self._handle("/start")
        mock_svc.create_restaurant.assert_not_called()

    def test_cancel_not_saved_as_restaurant(self):
        _, _, _, _, _, mock_svc = self._handle("cancel")
        mock_svc.create_restaurant.assert_not_called()


# ---------------------------------------------------------------------------
# Step 3: idempotency — retry / re-delivery
# ---------------------------------------------------------------------------

class TestStep3Idempotency:
    def test_reuses_existing_restaurant_on_retry(self):
        """If a restaurant already exists, create_restaurant must NOT be called."""
        user = make_user(full_name="Ali")
        ctx_svc = make_ctx_svc(context={"onboarding_step": "awaiting_restaurant"})
        settings = make_settings()
        db = MagicMock()

        existing_restaurant = MagicMock()
        existing_restaurant.id = uuid.uuid4()
        existing_restaurant.name = "Burger Barn"

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send, \
             patch("app.telegram.handlers.onboarding.RestaurantService") as MockRestaurantService, \
             patch("app.telegram.handlers.onboarding._get_existing_restaurant_for_user") as mock_get_existing:

            mock_svc = MagicMock()
            MockRestaurantService.return_value = mock_svc
            mock_get_existing.return_value = existing_restaurant  # Existing restaurant found

            handle(text_update("Burger Barn"), user, db, ctx_svc, settings)

            mock_svc.create_restaurant.assert_not_called()
            ctx_svc.set_active_restaurant.assert_called_once()
            all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
            assert all_kwargs.get("onboarding_step") is None


# ---------------------------------------------------------------------------
# Unknown / corrupted step — recovery
# ---------------------------------------------------------------------------

class TestUnknownStep:
    def test_clears_bad_step_and_restarts(self):
        user = make_user(full_name="Ali")
        ctx_svc = make_ctx_svc(context={"onboarding_step": "some_garbage_value"})
        settings = make_settings()
        db = MagicMock()

        with patch("app.telegram.handlers.onboarding.send_message") as mock_send:
            handle(text_update("whatever"), user, db, ctx_svc, settings)

        mock_send.assert_called_once()
        text_sent = mock_send.call_args[1].get("text") or mock_send.call_args[0][1]
        assert "start over" in text_sent.lower() or "went wrong" in text_sent.lower()

        all_kwargs = {k: v for call_ in ctx_svc.set_fields.call_args_list for k, v in call_[1].items()}
        assert all_kwargs.get("onboarding_step") == "awaiting_name"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

class AnyArg:
    """Matches any value in assert_called_once_with."""
    def __eq__(self, other):
        return True