from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep
from app.domain.services.user_service import UserService
from app.schemas.user import TelegramUserCreate, UserRead

router = APIRouter()


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db_dep)) -> list[UserRead]:
    return UserService(db).list_users()


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: TelegramUserCreate, db: Session = Depends(get_db_dep)) -> UserRead:
    return UserService(db).create_user(payload)
