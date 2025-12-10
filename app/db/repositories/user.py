from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self) -> Sequence[User]:
        return self.session.scalars(select(User)).all()

    def get(self, user_id) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.session.scalar(select(User).where(User.email == email))

    def create(self, obj_in: UserCreate) -> User:
        user = User(email=obj_in.email, hashed_password=obj_in.password)
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user

    def update(self, user: User, obj_in: UserUpdate) -> User:
        for field, value in obj_in.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        self.session.add(user)
        self.session.commit()
        self.session.refresh(user)
        return user
