from fastapi import APIRouter

from app.api.v1.routes import auth, restaurants, telegram

api_router = APIRouter()
api_router.include_router(telegram.router, prefix="/v1", tags=["telegram"])
api_router.include_router(auth.router, prefix="/v1", tags=["auth"])
api_router.include_router(restaurants.router, prefix="/v1", tags=["restaurants"])
