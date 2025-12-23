from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """FastAPI application factory so we can reuse the app in tests and lambdas."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name, version=settings.version, debug=settings.debug
    )

    cors_origins: list[str] = [str(o) for o in (settings.cors_origins or [])]
    if cors_origins or settings.debug:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins or ["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", tags=["health"])
    def index() -> dict[str, str]:
        return {"status": "ok", "message": "Server running"}

    return app


# Uvicorn entrypoint
app = create_app()
