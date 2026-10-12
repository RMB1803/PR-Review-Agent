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
    tiger_embedding_dim: int = 256

    # Google Gemini
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    # Security and review agents
    security_provider: str = "gemini"
    security_model: str = "gemini-2.5-flash"

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    max_concurrent_reviews: int = 3
    workflow_timeout_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()