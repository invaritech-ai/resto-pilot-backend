import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.repositories.user import UserRepository
from app.schemas.user import TelegramUserCreate, UserRead

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = UserRepository(session)

    def list_users(self) -> list[UserRead]:
        return [UserRead.model_validate(user) for user in self.repo.list()]

    def create_user(self, payload: TelegramUserCreate) -> UserRead:
        try:
            user = self.repo.create(payload)
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
            user = self.repo.get_by_telegram_id(payload.telegram_id)
            if user is None:
                user = self.repo.create(payload)
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
            user = self.repo.get_by_telegram_id(payload.telegram_id)
            if user is None:
                raise
            return UserRead.model_validate(user)

        except Exception as e:
            self.session.rollback()
            logger.exception("user_get_or_create_failed", extra={"error": repr(e)})
            raise
