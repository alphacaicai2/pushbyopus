"""Discord Webhook 客户端模块。

提供异步 HTTP 客户端，用于将 Miniflux 条目发送到 Discord Webhook。
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx


# =============================================================================
# 常量配置
# =============================================================================

DEFAULT_TIMEOUT = httpx.Timeout(timeout=20.0, connect=5.0)
"""默认超时配置：总超时 20 秒，连接超时 5 秒。"""

DEFAULT_MAX_RETRIES = 3
"""默认最大重试次数（包含首次请求）。"""

DEFAULT_BASE_BACKOFF_SECONDS = 0.5
"""指数退避基础延迟（秒）。"""

DEFAULT_MAX_BACKOFF_SECONDS = 30.0
"""单次重试最大等待时长（秒）。"""

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
"""可重试的 HTTP 状态码。"""

HTML_TAG_RE = re.compile(r"<[^>]+>")
"""用于移除 HTML 标签的正则。"""

WHITESPACE_RE = re.compile(r"\s+")
"""用于压缩连续空白字符的正则。"""

WEBHOOK_URL_RE = re.compile(r"^https://discord\.com/api/webhooks/(\d+)/([^/]+)")
"""用于解析和脱敏 Discord Webhook URL 的正则。"""

DISCORD_CONTENT_LIMIT = 2000
"""Discord 单条消息内容最大长度。"""


# =============================================================================
# 工具函数
# =============================================================================

def sanitize_webhook_url(url: str | None) -> str | None:
    """脱敏 Discord Webhook URL，隐藏 token 部分。

    Args:
        url: 原始 webhook URL。

    Returns:
        脱敏后的 URL，token 被替换为 "***"。
        如果 URL 格式不匹配，返回 "<webhook-redacted>"。
        如果输入为 None 或空，返回 None。
    """
    if not url:
        return None
    match = WEBHOOK_URL_RE.match(url)
    if match:
        channel_id = match.group(1)
        return f"https://discord.com/api/webhooks/{channel_id}/**{{REDACTED}}**"
    return "<webhook-redacted>"


def _sanitize_webhook_url(url: str) -> str:
    """脱敏 Discord Webhook URL（内部兼容版本）。"""
    result = sanitize_webhook_url(url)
    return result or "<webhook-redacted>"


# =============================================================================
# 异常类
# =============================================================================

class DiscordWebhookClientError(Exception):
    """Discord Webhook 客户端基础异常类。"""


class DiscordWebhookNetworkError(DiscordWebhookClientError):
    """网络层面错误（超时、DNS、连接失败等）。

    通常表示重试可能有价值。
    """


class DiscordWebhookAPIError(DiscordWebhookClientError):
    """API 层面错误（HTTP 状态码非 2xx 等）。

    Attributes:
        status_code: HTTP 状态码。
        url: 请求 URL（已脱敏）。
        retry_after: 服务端建议重试等待秒数（若有）。
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        url: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.retry_after = retry_after

        detail = f"Discord Webhook API 错误 [{status_code}]: {message}"
        if retry_after is not None:
            detail = f"{detail} (retry_after={retry_after:.3f}s)"
        if url:
            # 脱敏：只显示 webhook 的 channel_id 部分，隐藏 token
            safe_url = _sanitize_webhook_url(url)
            detail = f"{detail} (URL: {safe_url})"
        super().__init__(detail)


# =============================================================================
# 客户端类
# =============================================================================

class DiscordWebhookClient:
    """Discord Webhook 异步客户端。

    使用 httpx.AsyncClient 进行 HTTP 通信，支持：
    - embed 格式消息
    - 429 / 5xx / 网络异常重试
    - Retry-After 优先策略

    Example:
        async with DiscordWebhookClient() as client:
            await client.send_entry(webhook_url, entry, category_name="AI")

    Attributes:
        max_retries: 最大重试次数。
        base_backoff_seconds: 指数退避基础延迟。
        max_backoff_seconds: 单次重试最大等待时长。
    """

    def __init__(
        self,
        *,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff_seconds: float = DEFAULT_BASE_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
    ) -> None:
        """初始化 Discord Webhook 客户端。

        Args:
            timeout: HTTP 请求超时配置。
            max_retries: 最大重试次数（包含首次请求）。
            base_backoff_seconds: 指数退避基础延迟（秒）。
            max_backoff_seconds: 单次重试最大等待时长（秒）。

        Raises:
            ValueError: 参数校验失败。
        """
        self._validate_positive_int("max_retries", max_retries)
        self._validate_positive_number("base_backoff_seconds", base_backoff_seconds)
        self._validate_positive_number("max_backoff_seconds", max_backoff_seconds)

        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "MinifluxDiscordPush/1.0",
            },
        )

    async def __aenter__(self) -> DiscordWebhookClient:
        """进入异步上下文管理器。"""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """退出异步上下文管理器并关闭连接。"""
        await self.aclose()

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端连接。

        在不使用上下文管理器时需要手动调用。
        """
        await self._client.aclose()

    async def send_entry(
        self,
        webhook_url: str,
        entry: Mapping[str, Any],
        *,
        category_name: str | None = None,
        display_timezone: str = "Asia/Shanghai",
    ) -> None:
        """发送单条 Miniflux 条目到 Discord Webhook（embed 格式）。

        Args:
            webhook_url: Discord Webhook 完整 URL。
            entry: Miniflux 条目字典。
            category_name: 分类名，用于 embed footer 展示。
            display_timezone: 显示时区（如 Asia/Shanghai）。

        Raises:
            ValueError: 参数校验失败。
            DiscordWebhookNetworkError: 网络层错误且重试后仍失败。
            DiscordWebhookAPIError: API 返回错误且不可重试或重试后仍失败。
        """
        url = webhook_url.strip()
        if not url:
            raise ValueError("webhook_url 不能为空")

        payload = self._build_payload(
            entry,
            category_name=category_name,
            display_timezone=display_timezone,
        )
        await self._post_json_with_retry(url=url, payload=payload)

    async def send_digest(
        self,
        webhook_url: str,
        content: str,
    ) -> None:
        """发送日报文本到 Discord Webhook。

        超长内容自动分片发送。

        Args:
            webhook_url: Discord Webhook 完整 URL。
            content: 日报内容（Markdown 格式）。

        Raises:
            ValueError: 参数校验失败。
            DiscordWebhookNetworkError: 网络层错误且重试后仍失败。
            DiscordWebhookAPIError: API 返回错误且不可重试或重试后仍失败。
        """
        url = webhook_url.strip()
        if not url:
            raise ValueError("webhook_url 不能为空")

        text = content.strip()
        if not text:
            raise ValueError("digest content 不能为空")

        chunks = self._split_text(text, DISCORD_CONTENT_LIMIT)
        for idx, chunk in enumerate(chunks, start=1):
            prefix = "" if len(chunks) == 1 else f"({idx}/{len(chunks)})\n"
            payload = {
                "content": f"{prefix}{chunk}",
                "allowed_mentions": {"parse": []},
            }
            await self._post_json_with_retry(url=url, payload=payload)

    # =========================================================================
    # 私有方法
    # =========================================================================

    def _build_payload(
        self,
        entry: Mapping[str, Any],
        *,
        category_name: str | None,
        display_timezone: str,
    ) -> dict[str, Any]:
        """构造 Discord Webhook 请求体。"""
        return {
            "embeds": [
                self._build_embed(
                    entry,
                    category_name=category_name,
                    display_timezone=display_timezone,
                )
            ],
            "allowed_mentions": {"parse": []},
        }

    async def _post_json_with_retry(
        self,
        *,
        url: str,
        payload: Mapping[str, Any],
    ) -> None:
        """发送 JSON 请求并处理重试逻辑。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._client.post(url, json=payload)
            except httpx.TimeoutException as exc:
                if attempt >= self.max_retries:
                    raise DiscordWebhookNetworkError(
                        f"请求超时: POST {_sanitize_webhook_url(url)}"
                    ) from exc
                await asyncio.sleep(self._compute_backoff_seconds(attempt))
                continue
            except httpx.RequestError as exc:
                if attempt >= self.max_retries:
                    raise DiscordWebhookNetworkError(
                        f"网络请求失败: POST {_sanitize_webhook_url(url)}: {exc}"
                    ) from exc
                await asyncio.sleep(self._compute_backoff_seconds(attempt))
                continue

            if 200 <= response.status_code < 300:
                return

            retry_after = self._extract_retry_after_seconds(response)
            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < self.max_retries
            ):
                await asyncio.sleep(self._compute_retry_delay(attempt, retry_after))
                continue

            raise DiscordWebhookAPIError(
                status_code=response.status_code,
                message=self._extract_error_message(response),
                url=str(response.request.url),
                retry_after=retry_after,
            )

    @staticmethod
    def _split_text(text: str, limit: int) -> list[str]:
        """将长文本按指定长度分片，尽量在换行符处切分。

        注意：预留分片前缀（如 "(1/3)\\n"）的空间。
        """
        # 预留分片前缀空间 "(99/99)\n" = 9 字符
        safe_limit = limit - 10
        if safe_limit < 100:
            safe_limit = limit

        if len(text) <= safe_limit:
            return [text]

        chunks: list[str] = []
        buf = text

        while len(buf) > safe_limit:
            # 尝试在换行符处切分
            split_at = buf.rfind("\n", 0, safe_limit)
            if split_at <= 0:
                # 没有合适的换行符，强制在 safe_limit 处切分
                split_at = safe_limit

            chunks.append(buf[:split_at].rstrip())
            buf = buf[split_at:].lstrip()

        if buf:
            chunks.append(buf)

        return chunks

    def _build_embed(
        self,
        entry: Mapping[str, Any],
        *,
        category_name: str | None,
        display_timezone: str,
    ) -> dict[str, Any]:
        """构造单条 embed。"""
        title = self._truncate_text(
            self._to_non_empty_str(entry.get("title")) or "Untitled",
            256,
        )
        entry_url = self._to_non_empty_str(entry.get("url"))
        published_at = self._to_non_empty_str(entry.get("published_at"))
        author = self._to_non_empty_str(entry.get("author"))
        feed_title = self._extract_feed_title(entry)

        # 优先使用 summary，否则从 content 中提取并清理 HTML
        summary = self._to_non_empty_str(entry.get("summary"))
        if summary:
            description = summary
        else:
            content = self._to_non_empty_str(entry.get("content")) or ""
            description = self._strip_html(content)
        description = self._truncate_text(description, 1200)

        embed: dict[str, Any] = {
            "type": "rich",
            "title": title,
            "footer": {
                "text": self._truncate_text(
                    f"Category: {category_name}" if category_name else "Miniflux",
                    2048,
                )
            },
        }
        if entry_url:
            embed["url"] = entry_url
        if description:
            embed["description"] = description
        if published_at:
            embed["timestamp"] = published_at

        fields: list[dict[str, Any]] = []
        if feed_title:
            fields.append(
                {
                    "name": "Feed",
                    "value": self._truncate_text(feed_title, 1024),
                    "inline": True,
                }
            )
        if author:
            fields.append(
                {
                    "name": "Author",
                    "value": self._truncate_text(author, 1024),
                    "inline": True,
                }
            )

        # Add dual time display field
        time_display = self._format_dual_time(published_at, display_timezone)
        if time_display:
            fields.append(
                {
                    "name": "发布时间",
                    "value": time_display,
                    "inline": False,
                }
            )

        if fields:
            embed["fields"] = fields

        return embed

    def _compute_retry_delay(self, attempt: int, retry_after: float | None) -> float:
        """计算重试等待时间（优先使用 Retry-After，绝对尊重服务端建议）。"""
        if retry_after is not None:
            # 绝对尊重 Retry-After，不做上限裁剪
            return max(retry_after, 0.0)
        return min(self._compute_backoff_seconds(attempt), self.max_backoff_seconds)

    def _compute_backoff_seconds(self, attempt: int) -> float:
        """计算指数退避 + 抖动延迟。"""
        base = self.base_backoff_seconds * (2 ** (attempt - 1))
        jitter = random.uniform(0.0, base * 0.3)
        return base + jitter

    @staticmethod
    def _extract_retry_after_seconds(response: httpx.Response) -> float | None:
        """从响应中提取 Retry-After（秒）。

        支持两种格式：
        - 秒数（如 "2"）
        - HTTP-date（如 "Wed, 21 Oct 2015 07:28:00 GMT"）
        """
        header_value = response.headers.get("Retry-After")
        if header_value:
            raw = header_value.strip()
            # 尝试解析为秒数
            try:
                retry_after = float(raw)
                if retry_after >= 0:
                    return retry_after
            except ValueError:
                pass

            # 尝试解析为 HTTP-date
            try:
                retry_at = parsedate_to_datetime(raw)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                delta = (retry_at - now).total_seconds()
                if delta >= 0:
                    return delta
            except (TypeError, ValueError, OverflowError):
                pass

        # 尝试从响应体解析
        try:
            payload = response.json()
        except ValueError:
            return None

        if isinstance(payload, dict):
            raw_retry_after = payload.get("retry_after")
            if isinstance(raw_retry_after, (int, float)) and not isinstance(raw_retry_after, bool):
                if raw_retry_after >= 0:
                    return float(raw_retry_after)
        return None

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """从错误响应中提取错误信息。"""
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or "未知 API 错误"

        if isinstance(payload, dict):
            for key in ("message", "error", "error_message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        if isinstance(payload, str) and payload.strip():
            return payload.strip()

        return response.text.strip() or "未知 API 错误"

    @staticmethod
    def _format_dual_time(published_at: str | None, display_timezone: str) -> str | None:
        """Format dual time display (UTC + local timezone).

        Args:
            published_at: ISO 8601 timestamp string from Miniflux.
            display_timezone: Target timezone for local display.

        Returns:
            Formatted string with date and time in both timezones.
        """
        if not published_at:
            return None

        try:
            # Parse ISO 8601 timestamp
            dt_utc = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)

            # Format UTC time with date
            utc_str = dt_utc.strftime("%Y-%m-%d %H:%M") + " UTC"

            # Convert to display timezone
            try:
                target_tz = ZoneInfo(display_timezone)
                dt_local = dt_utc.astimezone(target_tz)
                # Get timezone abbreviation for display
                tz_abbr = dt_local.strftime("%Z") or display_timezone.split("/")[-1]
                local_str = dt_local.strftime("%Y-%m-%d %H:%M") + f" {tz_abbr}"
                return f"{utc_str} | {local_str}"
            except Exception:
                # Fallback if timezone conversion fails
                return utc_str

        except (ValueError, TypeError):
            return None

    @staticmethod
    def _extract_feed_title(entry: Mapping[str, Any]) -> str | None:
        """提取条目所属 feed 名称。"""
        feed = entry.get("feed")
        if isinstance(feed, Mapping):
            return DiscordWebhookClient._to_non_empty_str(feed.get("title"))
        return None

    @staticmethod
    def _strip_html(text: str) -> str:
        """移除 HTML 标签并压缩空白。"""
        no_tags = HTML_TAG_RE.sub(" ", text)
        compact = WHITESPACE_RE.sub(" ", no_tags)
        return compact.strip()

    @staticmethod
    def _truncate_text(text: str, max_length: int) -> str:
        """按最大长度截断文本。"""
        if len(text) <= max_length:
            return text
        return f"{text[: max_length - 3]}..."

    @staticmethod
    def _to_non_empty_str(value: Any) -> str | None:
        """将值转换为非空字符串。"""
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return None

    @staticmethod
    def _validate_positive_int(name: str, value: Any) -> None:
        """校验参数为正整数。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} 必须是整数，收到: {type(value).__name__}")
        if value <= 0:
            raise ValueError(f"{name} 必须为正整数，收到: {value}")

    @staticmethod
    def _validate_positive_number(name: str, value: Any) -> None:
        """校验参数为正数。"""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} 必须是数字，收到: {type(value).__name__}")
        if value <= 0:
            raise ValueError(f"{name} 必须大于 0，收到: {value}")
