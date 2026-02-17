import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.schemas.user import TelegramUserCreate, UserRead

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _list(self) -> list[User]:
        return list(self.session.scalars(select(User)).all())

    def _get_by_telegram_id(self, telegram_id: int) -> User | None:
        return self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    def _create(self, payload: TelegramUserCreate) -> User:
        full_name_parts = [p for p in [payload.first_name, payload.last_name] if p]
        full_name = " ".join(full_name_parts).strip() or None
        user = User(
            telegram_id=payload.telegram_id,
            chat_id=payload.chat_id,
            full_name=full_name,
            username=payload.username,
        )
        self.session.add(user)
        self.session.flush()
        return user

    def list_users(self) -> list[UserRead]:
        return [UserRead.model_validate(user) for user in self._list()]

    def create_user(self, payload: TelegramUserCreate) -> UserRead:
        try:
            user = self._create(payload)
            self.session.commit()
            return UserRead.model_validate(user)
        except Exception:
            self.session.rollback()
            logger.exception("user_create_failed")
            raise

    def get_or_create(self, payload: TelegramUserCreate) -> UserRead:
        full_name_parts = [p for p in [payload.first_name, payload.last_name] if p]
        full_name = " ".join(full_name_parts).strip() or None
        try:
            user = self._get_by_telegram_id(payload.telegram_id)
            if user is None:
                user = self._create(payload)
            else:
                user.chat_id = payload.chat_id
                user.full_name = full_name
                user.username = payload.username
                self.session.add(user)

            self.session.commit()
            return UserRead.model_validate(user)

        except IntegrityError as e:
            self.session.rollback()
            logger.exception("user_integrity_error", extra={"error": repr(e)})
            user = self._get_by_telegram_id(payload.telegram_id)
            if user is None:
                raise
            return UserRead.model_validate(user)

        except Exception as e:
            self.session.rollback()
            logger.exception("user_get_or_create_failed", extra={"error": repr(e)})
            raise
