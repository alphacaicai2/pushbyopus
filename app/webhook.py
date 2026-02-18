"""Miniflux Webhook 处理模块。

接收 Miniflux 推送的新条目，实时转发到 Discord。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.discord import (
    DiscordWebhookAPIError,
    DiscordWebhookClient,
    DiscordWebhookClientError,
    DiscordWebhookNetworkError,
    sanitize_webhook_url,
)
from app.miniflux import MinifluxClient
from app.models import CategoryBinding, DeliveryLog, PollSetting


logger = logging.getLogger(__name__)


class WebhookPayload(BaseModel):
    """Miniflux webhook payload（仅建模当前需要字段）。"""

    model_config = ConfigDict(extra="allow")

    feed: dict[str, Any] = Field(default_factory=dict)
    entries: list[dict[str, Any]] = Field(default_factory=list)


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """校验 Miniflux webhook HMAC-SHA256 签名。

    Args:
        raw_body: 原始请求体字节。
        signature_header: X-Miniflux-Signature header 值。
        secret: 配置的 webhook secret。

    Returns:
        签名是否有效。
    """
    if not signature_header:
        return False

    normalized_secret = secret.strip()
    if not normalized_secret:
        return False

    signature = signature_header.strip()
    # 支持 "sha256=xxx" 格式
    if "=" in signature:
        prefix, value = signature.split("=", 1)
        if prefix.lower() == "sha256":
            signature = value.strip()

    expected = hmac.new(
        normalized_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature.lower(), expected.lower())


class WebhookHandler:
    """处理 Miniflux webhook 事件。

    流程：
    1. 收到 new_entries 事件
    2. 从 feed_id 查询 Miniflux API 获取 category_id
    3. 查询本地 binding 配置
    4. 逐条推送到 Discord
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        discord_client: DiscordWebhookClient | None = None,
    ) -> None:
        """初始化 WebhookHandler。

        Args:
            session: 数据库会话。
            discord_client: 可选的 Discord 客户端（用于测试注入）。
        """
        self.session = session
        self._discord_client = discord_client or DiscordWebhookClient(max_retries=3)
        self._owns_discord_client = discord_client is None
        self._display_timezone: str | None = None

    async def aclose(self) -> None:
        """释放资源。"""
        if self._owns_discord_client:
            await self._discord_client.aclose()

    async def handle_new_entries(self, payload: WebhookPayload) -> dict[str, Any]:
        """处理 new_entries 事件并逐条推送到 Discord。

        Args:
            payload: Webhook payload。

        Returns:
            处理结果摘要。
        """
        if not payload.entries:
            return {"status": "ignored", "reason": "no_entries", "total": 0, "pushed": 0, "failed": 0}

        feed_id = self._extract_feed_id(payload)
        if feed_id is None:
            return {"status": "ignored", "reason": "missing_feed_id", "total": len(payload.entries), "pushed": 0, "failed": 0}

        category_id = await self._resolve_category_id(feed_id)
        if category_id is None:
            return {"status": "ignored", "reason": "missing_category_id", "total": len(payload.entries), "pushed": 0, "failed": 0}

        binding = await self._get_realtime_binding(category_id)
        if binding is None:
            return {"status": "ignored", "reason": "binding_not_enabled", "total": len(payload.entries), "pushed": 0, "failed": 0}

        webhook_url = (binding.webhook_url or "").strip()
        if not webhook_url:
            return {"status": "ignored", "reason": "missing_webhook_url", "total": len(payload.entries), "pushed": 0, "failed": 0}

        display_timezone = await self._get_display_timezone()

        pushed = 0
        failed = 0
        for raw_entry in payload.entries:
            entry = dict(raw_entry)
            # 确保 entry 中包含 feed 信息
            if "feed" not in entry and payload.feed:
                entry["feed"] = payload.feed
            entry.setdefault("feed_id", feed_id)

            entry_id = self._extract_optional_int(entry.get("id")) or 0
            try:
                await self._discord_client.send_entry(
                    webhook_url=webhook_url,
                    entry=entry,
                    category_name=binding.category_name,
                    display_timezone=display_timezone,
                )
                self._add_delivery_log(
                    entry=entry,
                    entry_id=entry_id,
                    binding=binding,
                    status="success",
                    error_message=None,
                )
                pushed += 1
                logger.info(
                    "Webhook 推送成功: category=%s entry_id=%s title=%s",
                    binding.category_name,
                    entry_id,
                    entry.get("title", "")[:50],
                )
            except (DiscordWebhookNetworkError, DiscordWebhookAPIError, DiscordWebhookClientError, ValueError) as exc:
                self._add_delivery_log(
                    entry=entry,
                    entry_id=entry_id,
                    binding=binding,
                    status="failed",
                    error_message=str(exc),
                )
                failed += 1
                logger.error(
                    "Webhook 推送失败: category_id=%s entry_id=%s error=%s",
                    binding.category_id,
                    entry_id,
                    exc,
                    exc_info=exc,
                )

        await self.session.commit()
        return {"status": "processed", "reason": None, "total": len(payload.entries), "pushed": pushed, "failed": failed}

    # =========================================================================
    # 私有方法
    # =========================================================================

    async def _resolve_category_id(self, feed_id: int) -> int | None:
        """通过 Miniflux API 查询 feed 所属的 category_id。"""
        base_url, token = await self._resolve_miniflux_credentials()
        if not base_url or not token:
            logger.warning("无法解析 category_id: Miniflux 凭据未配置")
            return None

        async with MinifluxClient(base_url=base_url, token=token) as client:
            feed = await client.get_feed(feed_id)

        # 优先从 category 对象获取
        category = feed.get("category")
        if isinstance(category, dict):
            category_id = self._extract_optional_int(category.get("id"))
            if category_id is not None:
                return category_id

        # 兼容 category_id 字段
        return self._extract_optional_int(feed.get("category_id"))

    async def _get_realtime_binding(self, category_id: int) -> CategoryBinding | None:
        """获取启用实时推送的 category binding。"""
        result = await self.session.execute(
            select(CategoryBinding).where(
                CategoryBinding.category_id == category_id,
                CategoryBinding.realtime_enabled.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _resolve_miniflux_credentials(self) -> tuple[str | None, str | None]:
        """从数据库读取 Miniflux 凭据。"""
        setting = await self.session.get(PollSetting, 1)
        if setting is None:
            return None, None

        base_url = None
        if isinstance(setting.miniflux_url, str) and setting.miniflux_url.strip():
            base_url = setting.miniflux_url.strip()

        token = None
        if isinstance(setting.miniflux_token, str) and setting.miniflux_token.strip():
            token = setting.miniflux_token.strip()

        return base_url, token

    async def _get_display_timezone(self) -> str:
        """获取显示时区。"""
        if self._display_timezone is not None:
            return self._display_timezone

        setting = await self.session.get(PollSetting, 1)
        if setting is not None and setting.display_timezone:
            self._display_timezone = setting.display_timezone
        else:
            self._display_timezone = "Asia/Shanghai"
        return self._display_timezone

    def _add_delivery_log(
        self,
        *,
        entry: dict[str, Any],
        entry_id: int,
        binding: CategoryBinding,
        status: str,
        error_message: str | None,
    ) -> None:
        """向数据库追加推送日志。"""
        feed_id = self._extract_optional_int(entry.get("feed_id"))
        if feed_id is None and isinstance(entry.get("feed"), dict):
            feed_id = self._extract_optional_int(entry["feed"].get("id"))

        log = DeliveryLog(
            entry_id=entry_id,
            category_id=binding.category_id,
            category_name=binding.category_name,
            feed_id=feed_id,
            title=self._extract_optional_text(entry.get("title")),
            url=self._extract_optional_text(entry.get("url")),
            webhook_url=sanitize_webhook_url(binding.webhook_url),
            mode="realtime",
            status=status,
            error_message=error_message,
            pushed_at=datetime.utcnow(),
        )
        self.session.add(log)

    @staticmethod
    def _extract_feed_id(payload: WebhookPayload) -> int | None:
        """从 payload 中提取 feed_id。"""
        # 优先从 feed 对象获取
        if isinstance(payload.feed, dict):
            for key in ("id", "feed_id"):
                value = payload.feed.get(key)
                parsed = WebhookHandler._extract_optional_int(value)
                if parsed is not None:
                    return parsed

        # 从 entries 中获取
        for entry in payload.entries:
            for key in ("feed_id",):
                parsed = WebhookHandler._extract_optional_int(entry.get(key))
                if parsed is not None:
                    return parsed
            if isinstance(entry.get("feed"), dict):
                parsed = WebhookHandler._extract_optional_int(entry["feed"].get("id"))
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _extract_optional_int(value: Any) -> int | None:
        """安全提取整数。"""
        if isinstance(value, int) and not isinstance(value, bool):
            return value if value >= 0 else None
        if isinstance(value, str) and value.strip():
            try:
                parsed = int(value)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    def _extract_optional_text(value: Any) -> str | None:
        """安全提取文本。"""
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None
