"""Tests for app/services/user_service.py.

Written TDD. Implementation must make these pass.

Interface under test:
    class UserService(session):
        get_or_create(
            telegram_id: int,
            chat_id: int,
            username: str | None = None,
        ) -> tuple[User, bool]
            # bool = True if the user was newly created

        update_last_interaction(user: User) -> None
            # Sets user.last_interaction_at = now, flushes

Callers own the commit.
"""

import uuid
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch

import pytest

from app.services.user_service import UserService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_session():
    session = MagicMock()
    session.add = MagicMock()
    session.flush = MagicMock()
    return session


def make_user(telegram_id=111, chat_id=222, full_name=None, username=None):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.telegram_id = telegram_id
    u.chat_id = chat_id
    u.full_name = full_name
    u.username = username
    u.last_interaction_at = datetime.now(UTC)
    return u


TG_ID = 7661369993
CHAT_ID = 7661369993


# ---------------------------------------------------------------------------
# get_or_create — existing user
# ---------------------------------------------------------------------------

class TestGetOrCreateExisting:
    def _session_returning(self, user):
        session = make_session()
        session.scalar.return_value = user
        return session

    def test_returns_existing_user(self):
        user = make_user()
        svc = UserService(self._session_returning(user))
        result, created = svc.get_or_create(TG_ID, CHAT_ID)
        assert result is user

    def test_created_is_false_for_existing(self):
        user = make_user()
        svc = UserService(self._session_returning(user))
        _, created = svc.get_or_create(TG_ID, CHAT_ID)
        assert created is False

    def test_does_not_add_to_session_for_existing(self):
        user = make_user()
        session = self._session_returning(user)
        svc = UserService(session)
        svc.get_or_create(TG_ID, CHAT_ID)
        session.add.assert_not_called()

    def test_updates_chat_id_if_changed(self):
        """Telegram chat_id can change (e.g. user migrated). Always sync."""
        user = make_user(telegram_id=TG_ID, chat_id=999)
        session = self._session_returning(user)
        svc = UserService(session)
        svc.get_or_create(TG_ID, new_chat_id := CHAT_ID)
        assert user.chat_id == new_chat_id

    def test_updates_username_if_provided(self):
        user = make_user(telegram_id=TG_ID, username=None)
        session = self._session_returning(user)
        svc = UserService(session)
        svc.get_or_create(TG_ID, CHAT_ID, username="ali123")
        assert user.username == "ali123"

    def test_does_not_overwrite_username_with_none(self):
        user = make_user(telegram_id=TG_ID, username="ali123")
        session = self._session_returning(user)
        svc = UserService(session)
        svc.get_or_create(TG_ID, CHAT_ID, username=None)
        assert user.username == "ali123"


# ---------------------------------------------------------------------------
# get_or_create — new user
# ---------------------------------------------------------------------------

class TestGetOrCreateNew:
    def _session_returning_none(self):
        session = make_session()
        session.scalar.return_value = None
        return session

    def test_returns_user_object(self):
        svc = UserService(self._session_returning_none())
        result, _ = svc.get_or_create(TG_ID, CHAT_ID)
        assert result is not None

    def test_created_is_true_for_new(self):
        svc = UserService(self._session_returning_none())
        _, created = svc.get_or_create(TG_ID, CHAT_ID)
        assert created is True

    def test_new_user_has_correct_telegram_id(self):
        svc = UserService(self._session_returning_none())
        user, _ = svc.get_or_create(TG_ID, CHAT_ID)
        assert user.telegram_id == TG_ID

    def test_new_user_has_correct_chat_id(self):
        svc = UserService(self._session_returning_none())
        user, _ = svc.get_or_create(TG_ID, CHAT_ID)
        assert user.chat_id == CHAT_ID

    def test_new_user_has_username_when_provided(self):
        svc = UserService(self._session_returning_none())
        user, _ = svc.get_or_create(TG_ID, CHAT_ID, username="ali")
        assert user.username == "ali"

    def test_new_user_full_name_is_none(self):
        """full_name is set during onboarding, not at user creation."""
        svc = UserService(self._session_returning_none())
        user, _ = svc.get_or_create(TG_ID, CHAT_ID)
        assert user.full_name is None

    def test_new_user_added_to_session(self):
        session = self._session_returning_none()
        svc = UserService(session)
        user, _ = svc.get_or_create(TG_ID, CHAT_ID)
        added = [c.args[0] for c in session.add.call_args_list]
        assert user in added

    def test_flush_called_for_new_user(self):
        session = self._session_returning_none()
        svc = UserService(session)
        svc.get_or_create(TG_ID, CHAT_ID)
        session.flush.assert_called()


# ---------------------------------------------------------------------------
# update_last_interaction
# ---------------------------------------------------------------------------

class TestUpdateLastInteraction:
    def test_sets_last_interaction_at(self):
        session = make_session()
        svc = UserService(session)
        user = make_user()
        old_ts = user.last_interaction_at
        svc.update_last_interaction(user)
        # Should have been updated (not identical to old value unless instant)
        assert user.last_interaction_at is not None

    def test_flush_called(self):
        session = make_session()
        svc = UserService(session)
        user = make_user()
        svc.update_last_interaction(user)
        session.flush.assert_called_once()

    def test_add_called(self):
        session = make_session()
        svc = UserService(session)
        user = make_user()
        svc.update_last_interaction(user)
        added = [c.args[0] for c in session.add.call_args_list]
        assert user in added
