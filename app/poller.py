"""轮询逻辑模块。

负责按分类增量拉取 Miniflux 条目，并投递到 Discord。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionFactory
from app.discord import (
    DiscordWebhookAPIError,
    DiscordWebhookClient,
    DiscordWebhookClientError,
    DiscordWebhookNetworkError,
    sanitize_webhook_url,
)
from app.miniflux import (
    MinifluxAPIError,
    MinifluxClient,
    MinifluxClientError,
    MinifluxNetworkError,
)
from app.models import CategoryBinding, DeliveryLog, DeliveryState, PollSetting


logger = logging.getLogger(__name__)


# =============================================================================
# 常量配置
# =============================================================================

DEFAULT_CATEGORY_CONCURRENCY = 3
"""默认分类并发度。"""

DEFAULT_STATE_COMMIT_BATCH_SIZE = 1
"""默认状态提交批大小（1 表示每条成功后提交，最稳妥）。"""


# =============================================================================
# 轮询器
# =============================================================================

class Poller:
    """Miniflux 分类轮询器。

    功能：
    - 轮询所有启用 realtime 的分类
    - 基于 DeliveryState.last_entry_id 增量拉取
    - 发送到 Discord 并记录 DeliveryLog
    - 成功后推进游标，失败即停止当前分类本轮处理

    Example:
        async with Poller(session) as poller:
            result = await poller.poll_all_categories()
            # result = {"AI 与深度学习": 3, "Web 与前端": 0}

    Attributes:
        session: 当前异步数据库会话。
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        max_concurrency: int = DEFAULT_CATEGORY_CONCURRENCY,
        state_commit_batch_size: int = DEFAULT_STATE_COMMIT_BATCH_SIZE,
        discord_client: DiscordWebhookClient | None = None,
        miniflux_client: MinifluxClient | None = None,
    ) -> None:
        """初始化轮询器。

        Args:
            session: 当前异步数据库会话。
            max_concurrency: `poll_all_categories` 分类并发上限。
            state_commit_batch_size: 游标状态批量提交大小（>=1）。
            discord_client: 可注入的 Discord 客户端（用于测试或共享）。
            miniflux_client: 可注入的 Miniflux 客户端（用于测试或共享）。

        Raises:
            ValueError: 参数校验失败。
        """
        self._validate_positive_int("max_concurrency", max_concurrency)
        self._validate_positive_int("state_commit_batch_size", state_commit_batch_size)

        self.session = session
        self._max_concurrency = max_concurrency
        self._state_commit_batch_size = state_commit_batch_size

        self._discord_client = discord_client or DiscordWebhookClient(max_retries=3)
        self._owns_discord_client = discord_client is None

        self._miniflux_client = miniflux_client
        self._owns_miniflux_client = False

        self._display_timezone: str | None = None

    async def __aenter__(self) -> Poller:
        """进入异步上下文管理器。"""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """退出异步上下文管理器并释放资源。"""
        await self.aclose()

    async def aclose(self) -> None:
        """关闭内部持有的客户端。

        注意：不会关闭外部注入的 session。
        """
        if self._owns_discord_client:
            await self._discord_client.aclose()
        if self._owns_miniflux_client and self._miniflux_client is not None:
            await self._miniflux_client.aclose()

    async def poll_all_categories(self) -> dict[str, int]:
        """轮询所有启用 realtime 的分类。

        使用有界并发（Semaphore）并为每个分类创建独立 DB 会话，
        避免在同一 AsyncSession 上并发操作。

        Returns:
            每个分类本轮"成功推进游标的条目数"。
            例如: {"AI 与深度学习": 3, "Web 与前端": 0}
        """
        result = await self.session.execute(
            select(CategoryBinding)
            .where(CategoryBinding.realtime_enabled.is_(True))
            .order_by(CategoryBinding.category_id.asc())
        )
        bindings = list(result.scalars().all())

        if not bindings:
            logger.info("没有启用实时推送的分类，本轮轮询结束")
            await self._touch_last_poll_at()
            return {}

        logger.info("开始轮询分类: count=%s", len(bindings))

        # 共享 Miniflux 客户端，避免每个分类重复创建连接池
        if self._miniflux_client is None:
            base_url, token = await self._resolve_miniflux_credentials()
            async with MinifluxClient(base_url=base_url, token=token) as shared_miniflux:
                summary = await self._poll_all_with_shared_clients(
                    bindings=bindings,
                    miniflux_client=shared_miniflux,
                )
        else:
            summary = await self._poll_all_with_shared_clients(
                bindings=bindings,
                miniflux_client=self._miniflux_client,
            )

        # 轮询流程完成即更新 last_poll_at（即使无新条目）
        await self._touch_last_poll_at()
        logger.info("分类轮询结束: summary=%s", summary)
        return summary

    async def poll_category(self, category_id: int) -> int:
        """轮询单个分类并执行实时推送。

        流程：
        1. 读取 DeliveryState.last_entry_id
        2. 拉取 entry_id > last_entry_id 的新条目
        3. 逐条调用 dispatch_entry
        4. 成功后更新 DeliveryState.last_entry_id
        5. 失败即停止本分类本轮处理

        Args:
            category_id: Miniflux 分类 ID。

        Returns:
            本轮"成功推进游标的条目数"。
        """
        self._validate_positive_int("category_id", category_id)

        binding = await self._get_realtime_binding(category_id)
        if binding is None:
            logger.info(
                "跳过分类：不存在或未启用 realtime_enabled: category_id=%s",
                category_id,
            )
            return 0

        webhook_url = (binding.webhook_url or "").strip()
        if not webhook_url:
            logger.warning("跳过分类：webhook_url 为空: category_id=%s", category_id)
            return 0

        state = await self._get_or_create_delivery_state(category_id)
        since_entry_id = state.last_entry_id
        logger.info(
            "开始轮询分类: category_id=%s category_name=%s since_entry_id=%s",
            category_id,
            binding.category_name,
            since_entry_id,
        )

        # 拉取新条目
        try:
            entries = await self._fetch_new_entries(
                category_id=category_id,
                since_entry_id=since_entry_id,
            )
        except MinifluxNetworkError as exc:
            logger.error(
                "Miniflux 网络错误: category_id=%s error=%s",
                category_id,
                exc,
                exc_info=exc,
            )
            return 0
        except MinifluxAPIError as exc:
            logger.error(
                "Miniflux API 错误: category_id=%s status=%s error=%s",
                category_id,
                exc.status_code,
                exc,
                exc_info=exc,
            )
            return 0
        except MinifluxClientError as exc:
            logger.error(
                "Miniflux 客户端错误: category_id=%s error=%s",
                category_id,
                exc,
                exc_info=exc,
            )
            return 0
        except asyncio.CancelledError:
            # 显式重新抛出取消信号，避免被吞掉
            raise
        except Exception as exc:
            logger.error(
                "未知异常（拉取 Miniflux）: category_id=%s error=%s",
                category_id,
                exc,
                exc_info=exc,
            )
            return 0

        if not entries:
            logger.info("分类无新条目: category_id=%s", category_id)
            return 0

        success_count = 0
        pending_state_updates = 0

        for entry in entries:
            entry_id = self._extract_entry_id(entry)
            ok = await self.dispatch_entry(entry, binding)
            if not ok:
                # 确保失败日志落库
                await self.session.commit()
                logger.warning(
                    "分类处理中止（发送失败）: category_id=%s failed_entry_id=%s success_count=%s",
                    category_id,
                    entry_id,
                    success_count,
                )
                break

            # 正常情况下 dispatch_entry 成功时 entry_id 必然有效；仍做防御性判断
            if entry_id is None:
                await self.session.commit()
                logger.error(
                    "发送成功但 entry_id 无效，停止推进游标: category_id=%s",
                    category_id,
                )
                break

            state.last_entry_id = max(state.last_entry_id, entry_id)
            state.last_push_at = datetime.utcnow()
            self.session.add(state)

            success_count += 1
            pending_state_updates += 1

            if pending_state_updates >= self._state_commit_batch_size:
                await self.session.commit()
                pending_state_updates = 0

        if pending_state_updates > 0:
            await self.session.commit()

        logger.info(
            "分类轮询完成: category_id=%s category_name=%s fetched=%s success=%s last_entry_id=%s",
            category_id,
            binding.category_name,
            len(entries),
            success_count,
            state.last_entry_id,
        )
        return success_count

    async def dispatch_entry(self, entry: dict[str, Any], binding: CategoryBinding) -> bool:
        """发送单条条目到 Discord，并写入 DeliveryLog。

        Args:
            entry: Miniflux 条目字典。
            binding: 分类绑定配置。

        Returns:
            发送成功返回 True，否则返回 False。
        """
        entry_id = self._extract_entry_id(entry)
        webhook_url = (binding.webhook_url or "").strip() or None

        if entry_id is None:
            self._add_delivery_log(
                entry=entry,
                entry_id=0,
                binding=binding,
                status="failed",
                error_message="entry.id 无效，无法投递",
            )
            logger.error("条目无效，无法投递: category_id=%s", binding.category_id)
            return False

        if webhook_url is None:
            self._add_delivery_log(
                entry=entry,
                entry_id=entry_id,
                binding=binding,
                status="failed",
                error_message="webhook_url 为空",
            )
            logger.error(
                "webhook_url 为空，无法投递: category_id=%s entry_id=%s",
                binding.category_id,
                entry_id,
            )
            return False

        try:
            display_tz = await self._get_display_timezone()
            await self._discord_client.send_entry(
                webhook_url=webhook_url,
                entry=entry,
                category_name=binding.category_name,
                display_timezone=display_tz,
            )
        except DiscordWebhookNetworkError as exc:
            self._add_delivery_log(
                entry=entry,
                entry_id=entry_id,
                binding=binding,
                status="failed",
                error_message=f"[network] {exc}",
            )
            logger.error(
                "Discord 网络错误: category_id=%s entry_id=%s error=%s",
                binding.category_id,
                entry_id,
                exc,
                exc_info=exc,
            )
            return False
        except DiscordWebhookAPIError as exc:
            self._add_delivery_log(
                entry=entry,
                entry_id=entry_id,
                binding=binding,
                status="failed",
                error_message=f"[api:{exc.status_code}] {exc}",
            )
            logger.error(
                "Discord API 错误: category_id=%s entry_id=%s status=%s error=%s",
                binding.category_id,
                entry_id,
                exc.status_code,
                exc,
                exc_info=exc,
            )
            return False
        except asyncio.CancelledError:
            # 显式重新抛出取消信号，避免被吞掉
            raise
        except (ValueError, DiscordWebhookClientError, Exception) as exc:
            self._add_delivery_log(
                entry=entry,
                entry_id=entry_id,
                binding=binding,
                status="failed",
                error_message=f"[unknown] {exc}",
            )
            logger.error(
                "Discord 未知错误: category_id=%s entry_id=%s error=%s",
                binding.category_id,
                entry_id,
                exc,
                exc_info=exc,
            )
            return False

        self._add_delivery_log(
            entry=entry,
            entry_id=entry_id,
            binding=binding,
            status="success",
            error_message=None,
        )
        return True

    # =========================================================================
    # 私有方法
    # =========================================================================

    async def _poll_all_with_shared_clients(
        self,
        *,
        bindings: list[CategoryBinding],
        miniflux_client: MinifluxClient,
    ) -> dict[str, int]:
        """使用共享客户端并发轮询分类。"""
        semaphore = asyncio.Semaphore(self._max_concurrency)
        categories = [(item.category_id, item.category_name) for item in bindings]

        tasks = [
            asyncio.create_task(
                self._poll_category_with_isolated_session(
                    category_id=category_id,
                    semaphore=semaphore,
                    miniflux_client=miniflux_client,
                )
            )
            for category_id, _ in categories
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        summary: dict[str, int] = {}
        for (_, category_name), raw in zip(categories, raw_results, strict=False):
            if isinstance(raw, Exception):
                logger.error(
                    "轮询分类失败: category_name=%s error=%s",
                    category_name,
                    raw,
                    exc_info=raw,
                )
                summary[category_name] = 0
            else:
                summary[category_name] = raw
        return summary

    async def _poll_category_with_isolated_session(
        self,
        *,
        category_id: int,
        semaphore: asyncio.Semaphore,
        miniflux_client: MinifluxClient,
    ) -> int:
        """在独立数据库会话中轮询单个分类。

        说明：共享 Discord / Miniflux 客户端，避免重复创建连接池。
        """
        async with semaphore:
            async with AsyncSessionFactory() as isolated_session:
                isolated_poller = Poller(
                    isolated_session,
                    max_concurrency=self._max_concurrency,
                    state_commit_batch_size=self._state_commit_batch_size,
                    discord_client=self._discord_client,
                    miniflux_client=miniflux_client,
                )
                return await isolated_poller.poll_category(category_id)

    async def _fetch_new_entries(
        self,
        *,
        category_id: int,
        since_entry_id: int,
    ) -> list[dict[str, Any]]:
        """拉取分类新条目。"""
        if self._miniflux_client is not None:
            return await self._miniflux_client.get_entries_since(
                category_id=category_id,
                since_entry_id=since_entry_id,
            )

        base_url, token = await self._resolve_miniflux_credentials()
        async with MinifluxClient(base_url=base_url, token=token) as temp_client:
            return await temp_client.get_entries_since(
                category_id=category_id,
                since_entry_id=since_entry_id,
            )

    async def _get_realtime_binding(self, category_id: int) -> CategoryBinding | None:
        """获取启用实时推送的分类绑定。"""
        result = await self.session.execute(
            select(CategoryBinding).where(
                CategoryBinding.category_id == category_id,
                CategoryBinding.realtime_enabled.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_delivery_state(self, category_id: int) -> DeliveryState:
        """获取或创建分类对应的 DeliveryState。"""
        result = await self.session.execute(
            select(DeliveryState).where(DeliveryState.category_id == category_id)
        )
        state = result.scalar_one_or_none()
        if state is None:
            state = DeliveryState(category_id=category_id, last_entry_id=0)
            self.session.add(state)
            await self.session.flush()
        return state

    async def _resolve_miniflux_credentials(self) -> tuple[str | None, str | None]:
        """从 PollSetting 读取覆盖配置（若存在）。"""
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
        """从 PollSetting 读取显示时区（带缓存）。"""
        if self._display_timezone is not None:
            return self._display_timezone

        setting = await self.session.get(PollSetting, 1)
        if setting is not None and setting.display_timezone:
            self._display_timezone = setting.display_timezone
        else:
            self._display_timezone = "Asia/Shanghai"

        return self._display_timezone

    async def _touch_last_poll_at(self) -> None:
        """更新 PollSetting.last_poll_at（表示轮询流程完成）。"""
        setting = await self.session.get(PollSetting, 1)
        if setting is None:
            setting = PollSetting(id=1)
        setting.last_poll_at = datetime.utcnow()
        self.session.add(setting)
        await self.session.commit()

    def _add_delivery_log(
        self,
        *,
        entry: dict[str, Any],
        entry_id: int,
        binding: CategoryBinding,
        status: str,
        error_message: str | None,
    ) -> None:
        """向会话追加一条 DeliveryLog。"""
        feed_id = self._extract_optional_int(entry.get("feed_id"))
        if feed_id is None and isinstance(entry.get("feed"), dict):
            feed_id = self._extract_optional_int(entry["feed"].get("id"))

        # 脱敏 webhook_url，避免敏感信息落库
        safe_webhook_url = sanitize_webhook_url(binding.webhook_url)

        log = DeliveryLog(
            entry_id=entry_id,
            category_id=binding.category_id,
            category_name=binding.category_name,
            feed_id=feed_id,
            title=self._extract_optional_text(entry.get("title")),
            url=self._extract_optional_text(entry.get("url")),
            webhook_url=safe_webhook_url,
            mode="realtime",
            status=status,
            error_message=error_message,
        )
        self.session.add(log)

    @staticmethod
    def _extract_entry_id(entry: dict[str, Any]) -> int | None:
        """提取条目 ID 并转换为整数。"""
        return Poller._extract_optional_int(entry.get("id"))

    @staticmethod
    def _extract_optional_int(value: Any) -> int | None:
        """提取可选整数值。"""
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
        """提取可选字符串值。"""
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
