import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.deps import get_db_dep, get_settings_dep
from app.api.security import (
    AccessTokenPayload,
    TelegramInitDataError,
    decode_access_token,
    encode_access_token,
    verify_telegram_webapp_init_data,
)
from app.core.config import Settings
from app.db.models.user import User
from app.services.restaurant_service import RestaurantService
from app.services.user_service import UserService
from app.schemas.user import TelegramUserCreate, UserRead

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


@router.post("/auth/telegram-webapp")
def telegram_webapp_auth(
    payload: dict,
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    init_data = payload.get("initData")
    if not isinstance(init_data, str) or not init_data.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="initData required")

    try:
        parsed = verify_telegram_webapp_init_data(init_data=init_data, settings=settings)
    except TelegramInitDataError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e

    user = parsed.get("user") or {}
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Telegram user")

    telegram_id: int = user["id"]
    db_user, _ = UserService(db).get_or_create(
        telegram_id=telegram_id,
        chat_id=telegram_id,
        username=user.get("username"),
    )
    # Set full_name from Telegram webapp data if not yet collected via onboarding
    if db_user.full_name is None:
        parts = [p for p in [user.get("first_name"), user.get("last_name")] if p]
        if parts:
            db_user.full_name = " ".join(parts).strip()
            db.add(db_user)
    db.commit()

    exp = int(time.time()) + settings.auth_token_ttl_seconds
    token = encode_access_token(
        payload=AccessTokenPayload(user_id=str(db_user.id), telegram_id=db_user.telegram_id, exp=exp),
        settings=settings,
    )
    return {"access_token": token, "token_type": "bearer", "user": UserRead.model_validate(db_user)}


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db_dep),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    try:
        payload = decode_access_token(token=creds.credentials, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e

    try:
        user_id = uuid.UUID(payload.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user_id") from e

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    if user.telegram_id != payload.telegram_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token mismatch")
    return user


@router.get("/me")
def me(
    db: Session = Depends(get_db_dep),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    rows = RestaurantService(db).list_for_user(user_id=current_user.id)
    return {
        "user": UserRead.model_validate(current_user),
        "restaurants": [
            {
                "id": str(restaurant.id),
                "name": restaurant.name,
                "is_owner": membership.is_owner,
                "is_active": membership.is_active,
            }
            for restaurant, membership in rows
        ],
    }
