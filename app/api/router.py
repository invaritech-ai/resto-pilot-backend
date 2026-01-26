from fastapi import APIRouter

from app.api.v1.routes import auth, files, restaurants, suppliers, telegram, users

api_router = APIRouter()
api_router.include_router(users.router, prefix="/v1", tags=["users"])
api_router.include_router(telegram.router, prefix="/v1", tags=["telegram"])
api_router.include_router(auth.router, prefix="/v1", tags=["auth"])
api_router.include_router(restaurants.router, prefix="/v1", tags=["restaurants"])
api_router.include_router(files.router, prefix="/v1", tags=["files"])
api_router.include_router(suppliers.router, prefix="/v1", tags=["suppliers"])
