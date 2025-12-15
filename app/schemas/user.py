from uuid import UUID

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    telegram_id: int = Field()
    chat_id: int
    first_name: str = Field(min_length=2)
    last_name: str = Field(min_length=2)


class UserRead(UserBase):
    id: UUID

    class Config:
        from_attributes = True
