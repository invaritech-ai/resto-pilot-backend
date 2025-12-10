from fastapi import APIRouter

from app.api.v1.routes import users, telegram

api_router = APIRouter()
api_router.include_router(users.router, prefix="/v1", tags=["users"])
api_router.include_router(telegram.router, prefix="/v1", tags=["telegram"])
