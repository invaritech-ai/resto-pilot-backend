from fastapi import APIRouter

from app.api.v1.routes import users

api_router = APIRouter()
api_router.include_router(users.router, prefix="/v1", tags=["users"])
