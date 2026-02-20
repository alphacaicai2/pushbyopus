# Miniflux → Discord 最小转发器接口契约

## 1. 范围

本契约用于 `translate`、`aggregate` **不启用** 的最小可用版本。
目标链路：`webhook ingest -> dedup -> router -> dispatch`。

## 2. DTO 定义（Python type hints）

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


MatchType = Literal["feed", "category"]


@dataclass(slots=True, frozen=True)
class EntryDTO:
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
    id: int
    match_type: MatchType
    match_value: str
    webhook_url: str
    enabled: bool
    created_at: datetime


@dataclass(slots=True, frozen=True)
class CreateRouteRuleInput:
    match_type: MatchType
    match_value: str
    webhook_url: str
    enabled: bool = True


@dataclass(slots=True, frozen=True)
class UpdateRouteRuleInput:
    match_type: MatchType | None = None
    match_value: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None


@dataclass(slots=True, frozen=True)
class RouteDecisionDTO:
    entry: EntryDTO
    webhook_urls: list[str]


@dataclass(slots=True, frozen=True)
class DiscordPayloadDTO:
    content: str
    embeds: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class DispatchResult:
    webhook_url: str
    success: bool
    status_code: int | None = None
    retryable: bool = False
    error: str | None = None
    delivered_at: datetime | None = None


@dataclass(slots=True, frozen=True)
class IngestReport:
    received: int
    dedup_passed: int
    routed: int
    dispatch_attempted: int
    dispatch_succeeded: int
    dispatch_failed: int
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class MinifluxWebhookEventDTO:
    event_id: str | None
    entries: list[EntryDTO]
    received_at: datetime
```

## 3. 服务接口函数签名

```python
from __future__ import annotations

from typing import Sequence
from sqlmodel import Session


# app/services/dedup.py
async def is_new_entry(*, db: Session, entry_url: str) -> bool: ...
async def mark_seen(*, db: Session, entry_url: str) -> None: ...
async def filter_new_entries(
    *, db: Session, entries: Sequence[EntryDTO]
) -> list[EntryDTO]: ...


# app/services/router.py
async def resolve_webhooks(*, db: Session, entry: EntryDTO) -> list[str]: ...
async def route_entries(
    *, db: Session, entries: Sequence[EntryDTO]
) -> list[RouteDecisionDTO]: ...

async def list_route_rules(*, db: Session, enabled_only: bool = False) -> list[RouteRuleDTO]: ...
async def create_route_rule(*, db: Session, data: CreateRouteRuleInput) -> RouteRuleDTO: ...
async def update_route_rule(
    *, db: Session, rule_id: int, data: UpdateRouteRuleInput
) -> RouteRuleDTO: ...
async def delete_route_rule(*, db: Session, rule_id: int) -> None: ...


# app/services/dispatch.py
def build_discord_payload(*, entry: EntryDTO) -> DiscordPayloadDTO: ...
async def dispatch_one(
    *,
    webhook_url: str,
    payload: DiscordPayloadDTO,
    timeout_s: float = 10.0,
    max_retries: int = 3,
) -> DispatchResult: ...
async def dispatch_many(
    *,
    webhook_urls: Sequence[str],
    payload: DiscordPayloadDTO,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    concurrency: int = 4,
) -> list[DispatchResult]: ...


# app/services/ingest.py
async def process_entries(*, db: Session, entries: Sequence[EntryDTO]) -> IngestReport: ...
async def process_webhook_event(
    *, db: Session, event: MinifluxWebhookEventDTO
) -> IngestReport: ...
```

## 4. 路由层接口签名（FastAPI）

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session


# app/routers/webhook.py
async def miniflux_webhook(request: Request, db: Session = Depends(...)) -> IngestReport: ...


# app/routers/admin.py
async def get_rules(db: Session = Depends(...)) -> list[RouteRuleDTO]: ...
async def post_rule(data: CreateRouteRuleInput, db: Session = Depends(...)) -> RouteRuleDTO: ...
async def put_rule(rule_id: int, data: UpdateRouteRuleInput, db: Session = Depends(...)) -> RouteRuleDTO: ...
async def delete_rule(rule_id: int, db: Session = Depends(...)) -> dict[str, bool]: ...
async def test_push(rule_id: int, db: Session = Depends(...)) -> list[DispatchResult]: ...
```

## 5. 配置与数据库基础接口

```python
# app/config.py
class Settings:
    app_env: str
    log_level: str
    database_url: str
    miniflux_webhook_secret: str | None
    discord_timeout_s: float
    discord_max_retries: int


def get_settings() -> Settings: ...


# app/db.py
def init_db() -> None: ...
def get_session() -> Session: ...
```

## 6. 统一约定

```python
# 去重键
dedup_key: str = EntryDTO.url  # URL 为唯一去重键

# 路由规则匹配
# match_type == "feed"      => str(entry.feed_id) == match_value
# match_type == "category"  => str(entry.category_id) == match_value

# DispatchResult.retryable 判定
# HTTP 429/5xx 或网络超时 => True
# 其余 4xx（除 429）      => False
```
