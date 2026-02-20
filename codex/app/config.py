"""
Configuration management using pydantic-settings.

Provides centralized configuration with environment variable support,
validation, and caching for optimal performance.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Final

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Constants for validation bounds
MIN_TIMEOUT_SECONDS: Final[float] = 1.0
MAX_TIMEOUT_SECONDS: Final[float] = 120.0
MIN_RETRIES: Final[int] = 0
MAX_RETRIES: Final[int] = 10
VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset({
    "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
})


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings can be overridden via environment variables or a .env file.
    Environment variables take precedence over .env file values.

    Attributes:
        app_env: Application environment (development, staging, production).
        log_level: Logging level for the application.
        database_url: Async SQLite connection string.
        miniflux_webhook_secret: Optional secret for validating Miniflux webhooks.
        discord_timeout_s: HTTP timeout for Discord API requests.
        discord_max_retries: Maximum retry attempts for failed Discord requests.
    """

    # Application settings
    app_env: str = Field(
        default="development",
        description="Application environment (development, staging, production)",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # Database settings
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/relay.db",
        description="Async SQLite connection string (must use sqlite+aiosqlite://)",
    )

    # Miniflux settings (optional for webhook-only mode)
    miniflux_url: str = Field(
        default="",
        description="Miniflux server URL (optional, for polling mode)",
    )
    miniflux_token: str = Field(
        default="",
        description="Miniflux API token (optional, for polling mode)",
    )
    miniflux_webhook_secret: str | None = Field(
        default=None,
        description="Optional secret for validating incoming Miniflux webhooks",
    )

    # Discord settings
    discord_timeout_s: float = Field(
        default=10.0,
        gt=0.0,
        le=MAX_TIMEOUT_SECONDS,
        description="HTTP timeout in seconds for Discord webhook requests",
    )
    discord_max_retries: int = Field(
        default=3,
        ge=MIN_RETRIES,
        le=MAX_RETRIES,
        description="Maximum number of retry attempts for failed Discord requests",
    )

    # Translation settings (硅基流动)
    translate_provider: str = Field(
        default="none",
        description="Translation provider: 'siliconflow' or 'none'",
    )
    siliconflow_api_key: str = Field(
        default="",
        description="SiliconFlow API key for translation",
    )
    siliconflow_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        description="SiliconFlow API base URL",
    )
    siliconflow_model: str = Field(
        default="Qwen/Qwen3-VL-32B-Instruct",
        description="SiliconFlow model for translation",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_and_normalize_log_level(cls, value: str) -> str:
        """Validate and normalize the log level to uppercase."""
        if not isinstance(value, str):
            raise TypeError("log_level must be a string")

        normalized = value.upper().strip()
        if normalized not in VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(VALID_LOG_LEVELS))
            raise ValueError(f"log_level must be one of: {allowed}, got: {value!r}")

        return normalized

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Ensure database URL uses the async SQLite driver."""
        if not isinstance(value, str):
            raise TypeError("database_url must be a string")

        if not value.startswith("sqlite+aiosqlite://"):
            raise ValueError(
                "database_url must use 'sqlite+aiosqlite://' scheme for async SQLite, "
                f"got: {value!r}"
            )

        return value

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.app_env.lower() in ("development", "dev", "local")

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env.lower() in ("production", "prod")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get the application settings instance.

    Uses lru_cache to ensure settings are only loaded once per process.
    The cached instance is returned on subsequent calls.

    Returns:
        Settings: The application settings instance.
    """
    return Settings()


def clear_settings_cache() -> None:
    """
    Clear the settings cache.

    This is primarily useful for testing scenarios where you need to
    reload settings with different environment variables.
    """
    get_settings.cache_clear()
