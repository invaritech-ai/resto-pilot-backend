"""User get-or-create and interaction tracking."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User


class UserService:
    def __init__(self, session: Session) -> None:
        self._db = session

    def get_or_create(
        self,
        telegram_id: int,
        chat_id: int,
        username: str | None = None,
    ) -> tuple[User, bool]:
        """Return (user, was_created). Caller owns the commit."""
        user = self._db.scalar(select(User).where(User.telegram_id == telegram_id))

        if user is not None:
            user.chat_id = chat_id
            if username is not None:
                user.username = username
            return user, False

        user = User(
            telegram_id=telegram_id,
            chat_id=chat_id,
            username=username,
            full_name=None,
        )
        self._db.add(user)
        self._db.flush()
        return user, True

    def update_last_interaction(self, user: User) -> None:
        user.last_interaction_at = dt.datetime.now(dt.UTC)
        self._db.add(user)
        self._db.flush()
