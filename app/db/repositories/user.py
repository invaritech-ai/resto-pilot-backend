from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.schemas.user import UserBase


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self) -> Sequence[User]:
        return self.session.scalars(select(User)).all()

    def get(self, user_id: UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return self.session.scalar(select(User).where(User.telegram_id == telegram_id))

    def create(self, obj_in: UserBase) -> User:
        full_name = f"{obj_in.first_name} {obj_in.last_name}".strip()
        user = User(
            telegram_id=obj_in.telegram_id,
            chat_id=obj_in.chat_id,
            full_name=full_name,
            username=None,
        )
        self.session.add(user)
        # self.session.commit()
        self.session.flush()
        return user

    def get_or_create_by_telegram_id(self, obj_in: UserBase) -> User:
        user: User | None = self.get_by_telegram_id(obj_in.telegram_id)
        if user is not None:
            return user
        return self.create(obj_in)
