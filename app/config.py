from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub
    github_webhook_secret: str = "test-secret"
    github_token: str = ""
    github_api_base_url: str = "https://api.github.com"
    review_body_max_characters: int = 65536

    # Redis / ARQ
    redis_url: str = "redis://localhost:6379"

    # Tiger Cloud PostgreSQL
    tiger_database_url: str

    # OpenAI
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-large"
    tiger_embedding_dim: int = 256

    # Security agent
    security_provider: str = "openai"
    security_model: str = "gpt-4o"

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    max_concurrent_reviews: int = 3
    workflow_timeout_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()