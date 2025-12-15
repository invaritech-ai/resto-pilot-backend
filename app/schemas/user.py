from uuid import UUID

from pydantic import BaseModel, Field


class TelegramUserCreate(BaseModel):
    telegram_id: int
    chat_id: int
    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = Field(default=None, min_length=1)
    username: str | None = None


class UserRead(BaseModel):
    id: UUID
    telegram_id: int
    chat_id: int
    full_name: str | None = None
    username: str | None = None

    class Config:
        from_attributes = True
