"""日报生成模块。

负责按分类聚合条目，调用 LLM 生成摘要，并发送到 Discord。
"""

from __future__ import annotations

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
from app.llm import DigestSummaryInput, LLMClientError, build_digest_llm_client
from app.miniflux import MinifluxClient
from app.models import CategoryBinding, DeliveryLog, DigestSetting, PollSetting


logger = logging.getLogger(__name__)


# =============================================================================
# 日报服务
# =============================================================================


class DigestService:
    """日报生成服务。

    功能：
    - 轮询所有启用 digest 的分类
    - 拉取过去 period_hours 小时内的条目
    - 调用 LLM 生成摘要
    - 发送到 Discord 并记录 DeliveryLog

    Example:
        async with DigestService(session) as service:
            result = await service.run_digest_once()
            # result = {"AI 与深度学习": 15, "Web 与前端": 8}

    Attributes:
        session: 当前异步数据库会话。
    """

    def __init__(self, session: AsyncSession) -> None:
        """初始化日报服务。

        Args:
            session: 当前异步数据库会话。
        """
        self.session = session

    async def run_digest_once(self) -> dict[str, int]:
        """执行一次日报生成流程。

        Returns:
            每个分类本轮处理的条目数。
            例如: {"AI 与深度学习": 15, "Web 与前端": 8}
        """
        setting = await self._get_or_create_digest_setting()
        bindings = await self._get_enabled_bindings()

        if not bindings:
            logger.info("没有启用日报的分类，本轮跳过")
            return {}

        base_url, token = await self._resolve_miniflux_credentials()
        summary: dict[str, int] = {}

        # 初始化 LLM 客户端，失败时降级为纯 fallback 模式
        llm_client = None
        use_fallback_only = False
        try:
            llm_client = build_digest_llm_client()
        except LLMClientError as exc:
            logger.warning("LLM 客户端初始化失败，使用 fallback 模式: %s", exc)
            use_fallback_only = True

        async with MinifluxClient(base_url=base_url, token=token) as miniflux_client:
            async with DiscordWebhookClient(max_retries=3) as discord_client:
                try:
                    for binding in bindings:
                        count = await self._process_one_category(
                            binding=binding,
                            setting=setting,
                            miniflux=miniflux_client,
                            discord=discord_client,
                            llm=llm_client,
                            use_fallback_only=use_fallback_only,
                        )
                        summary[binding.category_name] = count
                finally:
                    if llm_client is not None:
                        await llm_client.aclose()

        logger.info("日报生成完成: %s", summary)
        return summary

    # =========================================================================
    # 私有方法
    # =========================================================================

    async def _process_one_category(
        self,
        *,
        binding: CategoryBinding,
        setting: DigestSetting,
        miniflux: MinifluxClient,
        discord: DiscordWebhookClient,
        llm: Any,
        use_fallback_only: bool = False,
    ) -> int:
        """处理单个分类的日报生成。"""
        webhook_url = (binding.webhook_url or "").strip()
        if not webhook_url:
            self._add_digest_log(
                entry_id=0,
                binding=binding,
                status="failed",
                title="(digest skipped)",
                url=None,
                error_message="webhook_url 为空",
            )
            await self.session.commit()
            logger.warning("跳过分类：webhook_url 为空: category_id=%s", binding.category_id)
            return 0

        # 拉取条目
        try:
            entries = await miniflux.get_entries_within_period(
                category_id=binding.category_id,
                period_hours=setting.period_hours,
            )
        except Exception as exc:
            logger.error(
                "拉取条目失败: category_id=%s error=%s",
                binding.category_id,
                exc,
                exc_info=exc,
            )
            self._add_digest_log(
                entry_id=0,
                binding=binding,
                status="failed",
                title="(digest failed)",
                url=None,
                error_message=f"拉取条目失败: {exc}",
            )
            await self.session.commit()
            return 0

        # 生成摘要
        if not entries:
            content = (
                f"## {binding.category_name} 日报\n\n"
                f"过去 {setting.period_hours} 小时暂无新增条目。"
            )
        elif use_fallback_only or llm is None:
            # LLM 不可用，直接使用 fallback
            content = self._build_fallback_summary(
                category_name=binding.category_name,
                period_hours=setting.period_hours,
                entries=entries,
            )
        else:
            req = DigestSummaryInput(
                category_name=binding.category_name,
                period_hours=setting.period_hours,
                summary_length=setting.summary_length,
                language_mode=setting.language_mode,
                entries=entries,
            )
            try:
                content = await llm.summarize(req)
                # 确保返回内容有效
                if not content or not isinstance(content, str):
                    raise LLMClientError("LLM 返回空内容")
            except (LLMClientError, AttributeError, TypeError) as exc:
                logger.warning(
                    "LLM 摘要失败，使用 fallback: category=%s err=%s",
                    binding.category_name,
                    exc,
                )
                content = self._build_fallback_summary(
                    category_name=binding.category_name,
                    period_hours=setting.period_hours,
                    entries=entries,
                )

        # 发送到 Discord
        try:
            await discord.send_digest(webhook_url=webhook_url, content=content)
        except DiscordWebhookNetworkError as exc:
            logger.error(
                "Discord 网络错误: category_id=%s error=%s",
                binding.category_id,
                exc,
                exc_info=exc,
            )
            for entry in entries[:10]:  # 最多记录 10 条失败日志
                self._add_digest_log(
                    entry_id=int(entry.get("id") or 0),
                    binding=binding,
                    status="failed",
                    title=entry.get("title"),
                    url=entry.get("url"),
                    error_message=f"[network] {exc}",
                )
            await self.session.commit()
            return 0
        except DiscordWebhookAPIError as exc:
            logger.error(
                "Discord API 错误: category_id=%s status=%s error=%s",
                binding.category_id,
                exc.status_code,
                exc,
                exc_info=exc,
            )
            for entry in entries[:10]:
                self._add_digest_log(
                    entry_id=int(entry.get("id") or 0),
                    binding=binding,
                    status="failed",
                    title=entry.get("title"),
                    url=entry.get("url"),
                    error_message=f"[api:{exc.status_code}] {exc}",
                )
            await self.session.commit()
            return 0
        except (ValueError, DiscordWebhookClientError, Exception) as exc:
            logger.error(
                "Discord 未知错误: category_id=%s error=%s",
                binding.category_id,
                exc,
                exc_info=exc,
            )
            for entry in entries[:10]:
                self._add_digest_log(
                    entry_id=int(entry.get("id") or 0),
                    binding=binding,
                    status="failed",
                    title=entry.get("title"),
                    url=entry.get("url"),
                    error_message=f"[unknown] {exc}",
                )
            await self.session.commit()
            return 0

        # 记录成功日志
        for entry in entries[:20]:  # 最多记录 20 条成功日志
            self._add_digest_log(
                entry_id=int(entry.get("id") or 0),
                binding=binding,
                status="success",
                title=entry.get("title"),
                url=entry.get("url"),
                error_message=None,
            )
        await self.session.commit()

        logger.info(
            "分类日报发送成功: category_id=%s category_name=%s count=%s",
            binding.category_id,
            binding.category_name,
            len(entries),
        )
        return len(entries)

    async def _get_enabled_bindings(self) -> list[CategoryBinding]:
        """获取所有启用日报的分类绑定。"""
        result = await self.session.execute(
            select(CategoryBinding)
            .where(CategoryBinding.digest_enabled.is_(True))
            .order_by(CategoryBinding.category_id.asc())
        )
        return list(result.scalars().all())

    async def _get_or_create_digest_setting(self) -> DigestSetting:
        """获取或创建日报设置（singleton）。"""
        setting = await self.session.get(DigestSetting, 1)
        if setting is None:
            setting = DigestSetting(id=1)
            self.session.add(setting)
            await self.session.commit()
            await self.session.refresh(setting)
        return setting

    async def _resolve_miniflux_credentials(self) -> tuple[str | None, str | None]:
        """从 PollSetting 读取 Miniflux 凭据。"""
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

    def _build_fallback_summary(
        self,
        *,
        category_name: str,
        period_hours: int,
        entries: list[dict[str, Any]],
    ) -> str:
        """构建降级摘要（LLM 失败时使用）。"""
        lines = [
            f"## {category_name} 日报",
            "",
            f"过去 {period_hours} 小时共 {len(entries)} 条：",
            "",
        ]

        for i, entry in enumerate(entries[:25], start=1):  # 最多显示 25 条
            title = (entry.get("title") or "Untitled").strip()
            url = (entry.get("url") or "").strip()
            lines.append(f"{i}. {title}")
            if url:
                lines.append(f"   - {url}")

        if len(entries) > 25:
            lines.append("")
            lines.append(f"... 等共 {len(entries)} 条")

        return "\n".join(lines)

    def _add_digest_log(
        self,
        *,
        entry_id: int,
        binding: CategoryBinding,
        status: str,
        title: str | None,
        url: str | None,
        error_message: str | None,
    ) -> None:
        """向会话追加一条 DeliveryLog。"""
        log = DeliveryLog(
            entry_id=entry_id,
            category_id=binding.category_id,
            category_name=binding.category_name,
            feed_id=None,
            title=title,
            url=url,
            webhook_url=sanitize_webhook_url(binding.webhook_url),
            mode="digest",
            status=status,
            error_message=error_message,
            pushed_at=datetime.utcnow(),
        )
        self.session.add(log)


# =============================================================================
# 便捷函数
# =============================================================================


async def run_digest() -> dict[str, int]:
    """执行一次日报生成的便捷函数。"""
    async with AsyncSessionFactory() as session:
        service = DigestService(session)
        return await service.run_digest_once()
