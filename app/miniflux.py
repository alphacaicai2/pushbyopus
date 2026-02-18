"""Miniflux API 客户端模块。

提供异步 HTTP 客户端，用于与 Miniflux RSS 服务端交互。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings


# =============================================================================
# 常量配置
# =============================================================================

DEFAULT_TIMEOUT = httpx.Timeout(timeout=20.0, connect=5.0)
"""默认超时配置：总超时 20 秒，连接超时 5 秒。"""

DEFAULT_ENTRIES_LIMIT = 100
"""get_entries 默认返回条目数。"""

MAX_ENTRIES_LIMIT = 500
"""单次请求最大条目数限制。"""

ALLOWED_DIRECTIONS = frozenset({"asc", "desc"})
"""允许的排序方向。"""


# =============================================================================
# 异常类
# =============================================================================

class MinifluxClientError(Exception):
    """Miniflux 客户端基础异常类。"""


class MinifluxNetworkError(MinifluxClientError):
    """网络层面错误（超时、DNS 解析、连接失败等）。

    通常表示重试可能有价值。
    """


class MinifluxAPIError(MinifluxClientError):
    """API 层面错误（HTTP 状态码非 2xx、响应格式错误等）。

    Attributes:
        status_code: HTTP 状态码。
        url: 请求的 URL。
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        url: str | None = None
    ) -> None:
        self.status_code = status_code
        self.url = url

        detail = f"Miniflux API 错误 [{status_code}]: {message}"
        if url:
            detail = f"{detail} (URL: {url})"
        super().__init__(detail)


# =============================================================================
# 客户端类
# =============================================================================

class MinifluxClient:
    """Miniflux API 异步客户端。

    使用 httpx.AsyncClient 进行 HTTP 通信，支持异步上下文管理器。

    Example:
        async with MinifluxClient(base_url, token) as client:
            categories = await client.get_categories()

    Attributes:
        base_url: Miniflux 服务基础 URL。
    """

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        """初始化 Miniflux 客户端。

        Args:
            base_url: Miniflux 服务地址，默认使用 settings.miniflux_url。
            token: Miniflux API Token，默认使用 settings.miniflux_token。

        Raises:
            ValueError: base_url 或 token 为空。
        """
        _url = (base_url or settings.miniflux_url).strip()
        _token = (token or settings.miniflux_token).strip()

        if not _url:
            raise ValueError("base_url 不能为空")
        if not _token:
            raise ValueError("token 不能为空")

        self.base_url = _url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=DEFAULT_TIMEOUT,
            headers={
                "X-Auth-Token": _token,
                "Accept": "application/json",
                "User-Agent": "MinifluxDiscordPush/1.0",
            },
        )

    async def __aenter__(self) -> MinifluxClient:
        """进入异步上下文管理器。"""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """退出异步上下文管理器，自动关闭连接。"""
        await self.aclose()

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端连接。

        在不使用上下文管理器时需要手动调用。
        """
        await self._client.aclose()

    async def get_categories(self) -> list[dict[str, Any]]:
        """获取所有分类列表。

        Returns:
            分类列表，每个分类包含 id、title 等字段。
            例如: [{"id": 2, "title": "AI 与深度学习"}, ...]

        Raises:
            MinifluxNetworkError: 网络请求失败。
            MinifluxAPIError: API 返回错误或响应格式异常。
        """
        payload = await self._request_json("GET", "/v1/categories")

        if not isinstance(payload, list):
            raise MinifluxAPIError(
                status_code=502,
                message=f"响应格式错误：期望列表，收到 {type(payload).__name__}",
            )

        categories: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise MinifluxAPIError(
                    status_code=502,
                    message=f"分类项格式错误：期望字典，收到 {type(item).__name__}",
                )
            categories.append(item)

        return categories

    async def get_feed(self, feed_id: int) -> dict[str, Any]:
        """获取单个 Feed 详情。

        Args:
            feed_id: Feed ID。

        Returns:
            Feed 详情字典（包含 category/category_id 等字段）。

        Raises:
            ValueError: 参数校验失败。
            MinifluxNetworkError: 网络请求失败。
            MinifluxAPIError: API 返回错误或响应格式异常。
        """
        self._validate_positive_int("feed_id", feed_id)

        payload = await self._request_json("GET", f"/v1/feeds/{feed_id}")
        if not isinstance(payload, dict):
            raise MinifluxAPIError(
                status_code=502,
                message=f"Feed 响应格式错误：期望字典，收到 {type(payload).__name__}",
            )
        return payload

    async def get_entries(
        self,
        category_id: int | None = None,
        limit: int = DEFAULT_ENTRIES_LIMIT,
        order: str = "published_at",
        direction: str = "desc",
        status: str = "unread",
    ) -> dict[str, Any]:
        """获取条目列表。

        Args:
            category_id: 分类 ID，为 None 时获取所有分类的条目。
            limit: 返回条目数量限制，默认 100，最大 500。
            order: 排序字段，默认 "published_at"（发布时间）。
            direction: 排序方向，"asc" 或 "desc"，默认 "desc"（最新在前）。
            status: 条目状态，默认 "unread"（未读），可选 "read"、"all" 等。

        Returns:
            包含 total 和 entries 的字典。
            例如: {"total": 150, "entries": [...]}

        Raises:
            ValueError: 参数校验失败。
            MinifluxNetworkError: 网络请求失败。
            MinifluxAPIError: API 返回错误或响应格式异常。
        """
        return await self._fetch_entries_page(
            category_id=category_id,
            limit=limit,
            order=order,
            direction=direction,
            status=status,
            offset=0,
        )

    async def get_entries_since(
        self,
        category_id: int,
        since_entry_id: int,
    ) -> list[dict[str, Any]]:
        """获取指定分类中 ID 大于 since_entry_id 的所有新条目。

        使用分页 + 早停策略，按 ID 降序获取直到遇到已知条目。

        Args:
            category_id: 分类 ID。
            since_entry_id: 起始条目 ID（不包含），获取 ID 大于此值的所有条目。

        Returns:
            新条目列表，按 ID 升序排列（时间顺序）。

        Raises:
            ValueError: 参数校验失败。
            MinifluxNetworkError: 网络请求失败。
            MinifluxAPIError: API 返回错误或响应格式异常。
        """
        self._validate_positive_int("category_id", category_id)
        self._validate_non_negative_int("since_entry_id", since_entry_id)

        collected: list[dict[str, Any]] = []
        offset = 0
        reached_boundary = False

        while not reached_boundary:
            page = await self._fetch_entries_page(
                category_id=category_id,
                limit=DEFAULT_ENTRIES_LIMIT,
                order="id",
                direction="desc",
                status="unread",  # 只获取未读条目
                offset=offset,
            )

            entries = page.get("entries", [])
            if not entries:
                break

            for entry in entries:
                entry_id = entry.get("id")
                if not isinstance(entry_id, int):
                    continue

                if entry_id > since_entry_id:
                    collected.append(entry)
                else:
                    # 已到达已知条目边界，停止分页
                    reached_boundary = True
                    break

            if len(entries) < DEFAULT_ENTRIES_LIMIT:
                # 最后一页，无需继续
                break

            offset += len(entries)

        # 按 ID 升序排列，确保时间顺序
        collected.sort(key=lambda e: e.get("id", 0))
        return collected

    async def get_entries_within_period(
        self,
        *,
        category_id: int,
        period_hours: int,
    ) -> list[dict[str, Any]]:
        """获取某分类在过去 period_hours 小时内的条目（已读+未读）。

        使用分页 + 早停策略，按发布时间降序获取直到超出时间窗口。

        Args:
            category_id: 分类 ID。
            period_hours: 时间窗口（小时）。

        Returns:
            条目列表，按发布时间升序排列。

        Raises:
            ValueError: 参数校验失败。
            MinifluxNetworkError: 网络请求失败。
            MinifluxAPIError: API 返回错误或响应格式异常。
        """
        self._validate_positive_int("category_id", category_id)
        self._validate_positive_int("period_hours", period_hours)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=period_hours)
        offset = 0
        collected: list[dict[str, Any]] = []

        while True:
            page = await self._fetch_entries_page(
                category_id=category_id,
                limit=DEFAULT_ENTRIES_LIMIT,
                order="published_at",
                direction="desc",
                status="all",  # 获取已读+未读条目
                offset=offset,
            )

            entries = page.get("entries", [])
            if not entries:
                break

            should_stop = False
            for entry in entries:
                published_at = self._parse_iso_datetime(entry.get("published_at"))
                # 有发布时间的条目：检查是否超出时间窗口
                # 无发布时间的条目：跳过时间检查，但计入收集
                if published_at is not None and published_at < cutoff:
                    should_stop = True
                    break
                collected.append(entry)

            if should_stop or len(entries) < DEFAULT_ENTRIES_LIMIT:
                break
            offset += len(entries)

        # 按发布时间升序排列
        collected.sort(
            key=lambda e: self._parse_iso_datetime(e.get("published_at"))
            or datetime.min.replace(tzinfo=timezone.utc)
        )
        return collected

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        """解析 ISO 8601 时间字符串。"""
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    # =========================================================================
    # 私有方法
    # =========================================================================

    async def _fetch_entries_page(
        self,
        *,
        category_id: int | None,
        limit: int,
        order: str,
        direction: str,
        status: str,
        offset: int,
    ) -> dict[str, Any]:
        """获取条目分页数据（内部方法）。"""
        self._validate_positive_int("limit", limit)
        self._validate_non_negative_int("offset", offset)

        if limit > MAX_ENTRIES_LIMIT:
            raise ValueError(f"limit 不能超过 {MAX_ENTRIES_LIMIT}")
        if direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"direction 必须是 'asc' 或 'desc'，收到: {direction!r}")
        if not order.strip():
            raise ValueError("order 不能为空")
        if not status.strip():
            raise ValueError("status 不能为空")

        params: dict[str, Any] = {
            "limit": limit,
            "order": order,
            "direction": direction,
            "status": status,
            "offset": offset,
        }

        if category_id is not None:
            params["category_id"] = category_id

        payload = await self._request_json("GET", "/v1/entries", params=params)

        if not isinstance(payload, dict):
            raise MinifluxAPIError(
                status_code=502,
                message=f"条目响应格式错误：期望字典，收到 {type(payload).__name__}",
            )

        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise MinifluxAPIError(
                status_code=502,
                message="条目响应缺少 'entries' 列表字段",
            )

        for i, item in enumerate(entries):
            if not isinstance(item, dict):
                raise MinifluxAPIError(
                    status_code=502,
                    message=f"条目 {i} 格式错误：期望字典，收到 {type(item).__name__}",
                )

        return payload

    async def _request_json(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """发送 HTTP 请求并解析 JSON 响应（内部方法）。"""
        try:
            response = await self._client.request(method, path, params=params)
        except httpx.TimeoutException as exc:
            raise MinifluxNetworkError(
                f"请求超时: {method} {path}"
            ) from exc
        except httpx.RequestError as exc:
            raise MinifluxNetworkError(
                f"网络请求失败: {method} {path}: {exc}"
            ) from exc

        if response.status_code >= 400:
            error_msg = self._extract_error_message(response)
            raise MinifluxAPIError(
                status_code=response.status_code,
                message=error_msg,
                url=str(response.request.url),
            )

        try:
            return response.json()
        except ValueError as exc:
            raise MinifluxAPIError(
                status_code=response.status_code,
                message="响应不是有效的 JSON 格式",
                url=str(response.request.url),
            ) from exc

    @staticmethod
    def _extract_error_message(response: httpx.Response) -> str:
        """从错误响应中提取错误信息。"""
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or "未知 API 错误"

        if isinstance(payload, dict):
            # Miniflux 常见错误字段
            for key in ("error_message", "message", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        if isinstance(payload, str) and payload.strip():
            return payload.strip()

        return "未知 API 错误"

    @staticmethod
    def _validate_positive_int(name: str, value: Any) -> None:
        """校验参数为正整数。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} 必须是整数，收到: {type(value).__name__}")
        if value <= 0:
            raise ValueError(f"{name} 必须为正整数，收到: {value}")

    @staticmethod
    def _validate_non_negative_int(name: str, value: Any) -> None:
        """校验参数为非负整数。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} 必须是整数，收到: {type(value).__name__}")
        if value < 0:
            raise ValueError(f"{name} 不能为负数，收到: {value}")


# =============================================================================
# 便捷函数
# =============================================================================

async def get_categories() -> list[dict[str, Any]]:
    """获取 Miniflux 分类列表的便捷函数。"""
    async with MinifluxClient() as client:
        return await client.get_categories()


async def get_new_entries(category_id: int, since_entry_id: int) -> list[dict[str, Any]]:
    """获取指定分类新条目的便捷函数。"""
    async with MinifluxClient() as client:
        return await client.get_entries_since(category_id, since_entry_id)
