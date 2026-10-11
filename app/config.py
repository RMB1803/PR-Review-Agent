# app/config.py
"""
Central configuration for PR-Review-Agent.
Uses pydantic-settings to read from environment variables and .env file.
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # GitHub
    github_webhook_secret: str = Field(default="test-secret")
    github_token: str = Field(default="")
    github_api_base_url: str = Field(default="https://api.github.com")
    review_body_max_characters: int = Field(default=65536)

    # Redis / ARQ
    redis_url: str = Field(default="redis://localhost:6379/0")

    # PostgreSQL / Tiger Cloud Database
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/pr_review_agent"
    )
    tiger_database_url: str = Field(
        default="",
        description="Tiger Cloud connection URL. Falls back to database_url if empty.",
    )
    tiger_pool_min: int = Field(default=2)
    tiger_pool_max: int = Field(default=10)

    # Embeddings
    openai_embedding_model: str = Field(default="text-embedding-3-large")
    openai_embedding_dimensions: int = Field(default=256)

    # LLM Providers
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")

    # Application Settings
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    max_concurrent_reviews: int = Field(default=3)
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    workflow_timeout_seconds: int = Field(default=300)
    api_key: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    return Settings()
