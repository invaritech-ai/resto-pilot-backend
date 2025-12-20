import datetime as dt
from uuid import UUID

from pydantic import BaseModel, Field


class InviteCreate(BaseModel):
    target_role: str = Field(pattern="^(owner|staff)$")
    expires_in_hours: int | None = Field(default=None, ge=1, le=24 * 14)


class InviteRead(BaseModel):
    code: str
    restaurant_id: UUID
    role: str
    expires_at: dt.datetime | None
    deep_link: str

