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
    telegram_batching_enabled: bool = False
    telegram_batch_idle_seconds: int = 30
    telegram_batch_max_seconds: int = 180

    # Optional AWS profile name for local dev.
    # If set, the app will use this profile for AWS SDK calls (including Celery SQS broker)
    # unless AWS_PROFILE is already set in the environment.
    aws_profile: str = ""

    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_sqs_region: str = ""
    celery_sqs_queue_url: str = ""
    celery_sqs_queue_name: str = "celery"
    celery_sqs_visibility_timeout_seconds: int = 60 * 30  # 30 minutes
    celery_sqs_wait_time_seconds: int = 10  # long polling (max 20)

    auth_secret: str = "dev-secret-change-me"
    auth_token_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days
    telegram_webapp_auth_max_age_seconds: int = 60 * 60 * 24  # 24h

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    # Optional overrides for dedicated "cheap" models.
    openai_ack_model: str = ""
    openai_gate_model: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    # This primarily controls the HTTP read timeout (how long we wait for the model response).
    # "Thinking" models can take 60-120s+, so keep this comfortably above expected latency.
    openai_timeout_seconds: float = 120.0
    openai_max_retries: int = 2
    openai_retry_initial_seconds: float = 0.5
    openai_retry_max_seconds: float = 4.0
    openrouter_http_referer: str = ""
    openrouter_title: str = ""

    # JSON mapping of model -> {input_per_million, output_per_million}
    # Used for local cost estimation from token usage.
    model_prices_json: str = ""

    # Comma-separated enabled capabilities for the main processing bot.
    # For now this is intentionally restrictive and defaults to a single capability.
    enabled_capabilities_csv: str = "inventory"

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
