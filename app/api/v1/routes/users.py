from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep
from app.domain.services.user_service import UserService
from app.schemas.user import UserCreate, UserRead

router = APIRouter()


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db_dep)) -> list[UserRead]:
    return UserService(db).list_users()


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db_dep)) -> UserRead:
    service = UserService(db)
    if service.repo.get_by_email(payload.email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    return service.create_user(payload)
