from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class TelegramUserCreate(BaseModel):
    telegram_id: int
    chat_id: int
    first_name: str | None = Field(default=None, min_length=1)
    last_name: str | None = Field(default=None, min_length=1)
    username: str | None = None

    @field_validator("first_name", "last_name", "username", mode="before")
    @classmethod
    def _normalize_optional_str(cls, value):  # type: ignore[no-untyped-def]
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class UserRead(BaseModel):
    id: UUID
    telegram_id: int
    chat_id: int
    full_name: str | None = None
    username: str | None = None

    class Config:
        from_attributes = True
