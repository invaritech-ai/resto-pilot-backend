from functools import lru_cache

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "resto-pilot"
    version: str = "0.1.0"
    debug: bool = True
    api_prefix: str = "/api"

    database_url: str = "sqlite:///./data/app.db"
    cors_origins: list[AnyHttpUrl] | list[str] = []

    telegram_bot_username: str = "MyBot"
    telegram_bot_token: str = ""
    telegram_webhook_secret_token: str = ""
    telegram_superuser_ids: list[int] = []

    auth_secret: str = "dev-secret-change-me"
    auth_token_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days
    telegram_webapp_auth_max_age_seconds: int = 60 * 60 * 24  # 24h

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
