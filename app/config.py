"""Application configuration management using pydantic-settings.

Settings are loaded from environment variables and optional .env file.
"""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Miniflux to Discord push system.

    Attributes:
        miniflux_url: Base URL of the Miniflux instance.
        miniflux_token: API token for Miniflux authentication.
        database_url: SQLAlchemy async database connection string.
        poll_interval_minutes: Default polling interval in minutes.
        llm_provider: LLM provider for digest summary (openai/anthropic).
        llm_timeout_seconds: LLM request timeout in seconds.
        openai_api_key: OpenAI API key.
        openai_model: OpenAI model name.
        openai_base_url: OpenAI API base URL.
        anthropic_api_key: Anthropic API key.
        anthropic_model: Anthropic model name.
    """

    miniflux_url: str = Field(
        default="https://miniflux.vibexcap.com",
        description="Miniflux instance URL",
    )
    miniflux_token: str = Field(
        default="",
        description="Miniflux API token",
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/push.db",
        description="Async database connection URL",
    )
    poll_interval_minutes: int = Field(
        default=15,
        ge=1,
        le=1440,
        description="Polling interval in minutes (1-1440)",
    )
    miniflux_webhook_secret: str = Field(
        default="",
        description="Secret used to verify Miniflux webhook signature",
    )

    # LLM Configuration
    llm_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="LLM provider for digest summary",
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        gt=1,
        le=180,
        description="LLM request timeout in seconds",
    )

    # OpenAI Configuration
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key",
    )
    openai_model: str = Field(
        default="gpt-4o-mini",
        description="OpenAI model name",
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI API base URL",
    )

    # Anthropic Configuration
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key",
    )
    anthropic_model: str = Field(
        default="claude-3-5-haiku-latest",
        description="Anthropic model name",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


"""Global settings instance."""
settings = Settings()
