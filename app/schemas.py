"""
Pydantic schemas for request/response validation.

This module defines all data transfer objects (DTOs) used by the API endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    "CategoryBindingBase",
    "CategoryBindingCreate",
    "CategoryBindingUpdate",
    "CategoryBindingResponse",
    "TestPushResponse",
    "DigestSettingUpdate",
    "DigestSettingResponse",
    "PollSettingUpdate",
    "PollSettingResponse",
    "SummaryLength",
    "LanguageMode",
    "PeriodHours",
]


# Type aliases for constrained values
SummaryLength = Literal["short", "medium", "long"]
LanguageMode = Literal["chinese", "english", "title_only", "original"]
PeriodHours = Literal[12, 24, 48]


class CategoryBindingBase(BaseModel):
    """Base fields for category binding configuration."""

    webhook_url: str | None = Field(
        default=None,
        description="Discord webhook URL for this category",
        examples=["https://discord.com/api/webhooks/..."],
    )
    realtime_enabled: bool = Field(
        default=True,
        description="Whether to enable realtime push for this category",
    )
    digest_enabled: bool = Field(
        default=False,
        description="Whether to include this category in daily digest",
    )


class CategoryBindingCreate(CategoryBindingBase):
    """Schema for creating a new category binding."""

    category_id: int = Field(..., description="Miniflux category ID", ge=1)
    category_name: str = Field(..., description="Miniflux category name", min_length=1)


class CategoryBindingUpdate(CategoryBindingBase):
    """Schema for updating an existing category binding. All fields are optional."""

    pass


class CategoryBindingResponse(CategoryBindingBase):
    """Schema for category binding response."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    category_name: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TestPushResponse(BaseModel):
    """Schema for single-category test push response.

    This response model provides detailed information about a test push attempt,
    including success/failure status, error details, and masked webhook URL for security.
    """

    status: Literal["success", "error"] = Field(
        ...,
        description="Operation status: 'success' if message was sent, 'error' otherwise",
    )
    category_id: int = Field(
        ...,
        description="Miniflux category ID that was tested",
        ge=1,
    )
    category_name: str | None = Field(
        default=None,
        description="Miniflux category name (null if category not found)",
    )
    message: str = Field(
        ...,
        description="Human-readable result message in Chinese",
    )
    webhook_url_masked: str | None = Field(
        default=None,
        description="Masked Discord webhook URL for security (token hidden)",
    )
    sent_at: datetime = Field(
        ...,
        description="UTC timestamp of the test attempt",
    )
    error_code: str | None = Field(
        default=None,
        description="Machine-readable error code if status is 'error'",
    )
    detail: str | None = Field(
        default=None,
        description="Detailed error message if status is 'error' (sanitized for security)",
    )


class DigestSettingUpdate(BaseModel):
    """Schema for updating digest settings. All fields are optional."""

    period_hours: PeriodHours | None = Field(
        default=None,
        description="Digest period in hours (12, 24, or 48)",
    )
    summary_length: SummaryLength | None = Field(
        default=None,
        description="AI summary length: short, medium, or long",
    )
    language_mode: LanguageMode | None = Field(
        default=None,
        description="Language mode for digest: chinese, english, title_only, or original",
    )
    digest_time: str | None = Field(
        default=None,
        description="Daily digest send time in HH:MM format",
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
    )
    timezone: str | None = Field(
        default=None,
        description="Digest timezone (e.g., Asia/Shanghai)",
        min_length=1,
        max_length=64,
    )


class DigestSettingResponse(BaseModel):
    """Schema for digest settings response."""

    model_config = ConfigDict(from_attributes=True)

    period_hours: PeriodHours
    summary_length: SummaryLength
    language_mode: LanguageMode
    digest_time: str
    timezone: str


class PollSettingUpdate(BaseModel):
    """Schema for updating poll settings. All fields are optional."""

    interval_minutes: int | None = Field(
        default=None,
        description="Polling interval in minutes",
        ge=1,
        le=1440,  # Max 24 hours
    )
    display_timezone: str | None = Field(
        default=None,
        description="Display timezone for timestamps (e.g., Asia/Shanghai)",
        min_length=1,
        max_length=64,
    )


class PollSettingResponse(BaseModel):
    """Schema for poll settings response."""

    model_config = ConfigDict(from_attributes=True)

    interval_minutes: int
    display_timezone: str = "Asia/Shanghai"
    last_poll_at: datetime | None = None
