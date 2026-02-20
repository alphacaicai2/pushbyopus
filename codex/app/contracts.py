"""
DTO definitions for Miniflux -> Discord relay service.

This module contains all data transfer objects defined in docs/contracts.md.
These are frozen dataclasses for immutability and performance (slots=True).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


# Type aliases for clarity
MatchType = Literal["feed", "category"]


@dataclass(slots=True, frozen=True)
class EntryDTO:
    """Represents a single RSS entry from Miniflux."""

    entry_id: int
    feed_id: int | None
    category_id: int | None
    title: str
    url: str
    author: str | None = None
    content: str | None = None
    published_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class RouteRuleDTO:
    """Represents a routing rule for directing entries to Discord webhooks."""

    id: int
    match_type: MatchType
    match_value: str
    webhook_url: str
    enabled: bool
    created_at: datetime


@dataclass(slots=True, frozen=True)
class CreateRouteRuleInput:
    """Input data for creating a new routing rule."""

    match_type: MatchType
    match_value: str
    webhook_url: str
    enabled: bool = True


@dataclass(slots=True, frozen=True)
class UpdateRouteRuleInput:
    """Input data for updating an existing routing rule."""

    match_type: MatchType | None = None
    match_value: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


@dataclass(slots=True, frozen=True)
class RouteDecisionDTO:
    """Represents the routing decision for an entry, including target webhooks."""

    entry: EntryDTO
    webhook_urls: list[str]


@dataclass(slots=True, frozen=True)
class DiscordPayloadDTO:
    """Payload structure for Discord webhook API."""

    content: str
    embeds: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class DispatchResult:
    """Result of a single Discord webhook dispatch attempt."""

    webhook_url: str
    success: bool
    status_code: int | None = None
    retryable: bool = False
    error: str | None = None
    delivered_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class IngestReport:
    """Report summarizing the processing of a batch of entries."""

    received: int
    dedup_passed: int
    routed: int
    dispatch_attempted: int
    dispatch_succeeded: int
    dispatch_failed: int
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class MinifluxWebhookEventDTO:
    """Represents an incoming webhook event from Miniflux."""

    event_id: str | None
    entries: list[EntryDTO]
    received_at: datetime
