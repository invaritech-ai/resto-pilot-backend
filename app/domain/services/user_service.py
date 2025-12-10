from sqlalchemy.orm import Session

from app.db.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserRead


class UserService:
    def __init__(self, session: Session) -> None:
        self.repo = UserRepository(session)

    def list_users(self) -> list[UserRead]:
        return [UserRead.model_validate(user) for user in self.repo.list()]

    def create_user(self, payload: UserCreate) -> UserRead:
        # NOTE: Hash passwords in core/security before production use.
        user = self.repo.create(payload)
        return UserRead.model_validate(user)
