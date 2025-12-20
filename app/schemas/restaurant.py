from uuid import UUID

from pydantic import BaseModel, Field, ConfigDict


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class RestaurantRead(BaseModel):
    id: UUID
    name: str
    model_config = ConfigDict(from_attributes=True)
    # class Config:
    #     from_attributes = True


class RestaurantMembershipRead(BaseModel):
    restaurant: RestaurantRead
    role: str
    status: str


class RestaurantMemberRead(BaseModel):
    user_id: UUID
    telegram_id: int
    full_name: str | None
    username: str | None
    role: str
    status: str
