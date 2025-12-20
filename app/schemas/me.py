from pydantic import BaseModel

from app.schemas.restaurant import RestaurantMembershipRead
from app.schemas.user import UserRead


class MeRead(BaseModel):
    user: UserRead
    restaurants: list[RestaurantMembershipRead]

