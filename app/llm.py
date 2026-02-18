"""LLM 客户端模块。

提供统一的 LLM 抽象接口，支持 OpenAI 和 Anthropic 两种 Provider。
用于生成 RSS 日报摘要。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


# =============================================================================
# 异常类
# =============================================================================


class LLMClientError(Exception):
    """LLM 客户端基础异常类。"""


# =============================================================================
# 数据类
# =============================================================================


@dataclass(slots=True)
class DigestSummaryInput:
    """日报摘要请求输入。

    Attributes:
        category_name: 分类名称。
        period_hours: 统计时间窗口（小时）。
        summary_length: 摘要长度（short/medium/long）。
        language_mode: 语言模式（chinese/english/title_only/original）。
        entries: 条目列表。
    """

    category_name: str
    period_hours: int
    summary_length: str
    language_mode: str
    entries: list[dict[str, Any]]


# =============================================================================
# 协议定义
# =============================================================================


class DigestLLMClient(Protocol):
    """日报摘要 LLM 客户端协议。"""

    async def summarize(self, req: DigestSummaryInput) -> str:
        """生成日报摘要。

        Args:
            req: 摘要请求输入。

        Returns:
            Markdown 格式的摘要文本。

        Raises:
            LLMClientError: LLM 调用失败。
        """
        ...

    async def aclose(self) -> None:
        """关闭客户端连接。"""
        ...


# =============================================================================
# Prompt 构建工具
# =============================================================================


def _build_length_instruction(mode: str) -> str:
    """构建摘要长度指令。"""
    if mode == "short":
        return "输出 3-5 个要点，总长度控制在 180-260 中文字（英文约 120-180 词）。"
    if mode == "long":
        return "输出 8-12 个要点，并给出趋势/共性观察，总长度 700-1000 中文字。"
    return "输出 5-8 个要点，总长度 350-550 中文字（英文约 220-350 词）。"


def _build_language_instruction(mode: str) -> str:
    """构建语言模式指令。"""
    mapping = {
        "chinese": "使用简体中文输出。",
        "english": "Use English for the whole output.",
        "title_only": "只翻译标题为目标语言，正文保持极简要点。",
        "original": "尽量保留原文语言，不强制翻译。",
    }
    return mapping.get(mode, "使用简体中文输出。")


def _build_messages(req: DigestSummaryInput) -> tuple[str, str]:
    """构建 system prompt 和 user prompt。"""
    # 压缩条目数据，只保留必要字段
    compact_entries = []
    for entry in req.entries[:80]:  # 最多处理 80 条
        feed_info = entry.get("feed")
        feed_title = None
        if isinstance(feed_info, dict):
            feed_title = feed_info.get("title")

        compact_entries.append(
            {
                "title": entry.get("title"),
                "url": entry.get("url"),
                "feed": feed_title,
                "published_at": entry.get("published_at"),
                "summary": (entry.get("summary") or "")[:280],
            }
        )

    system_prompt = (
        "你是资讯日报助手。输出 Markdown，结构固定为：\n"
        "1) 一句话总览\n2) 要点列表（每个要点包含标题和简短说明）\n3) 链接列表\n"
        "不要编造未给出的事实。保持客观、简洁。"
    )

    user_prompt = (
        f"分类: {req.category_name}\n"
        f"统计窗口: 过去 {req.period_hours} 小时\n"
        f"条目数量: {len(req.entries)}\n\n"
        f"要求:\n"
        f"- {_build_length_instruction(req.summary_length)}\n"
        f"- {_build_language_instruction(req.language_mode)}\n\n"
        f"条目数据:\n{json.dumps(compact_entries, ensure_ascii=False, indent=2)}"
    )

    return system_prompt, user_prompt


# =============================================================================
# OpenAI 客户端
# =============================================================================


class OpenAIDigestClient:
    """OpenAI 日报摘要客户端。

    使用 httpx.AsyncClient 调用 OpenAI Chat Completions API。

    Attributes:
        _client: HTTP 客户端实例。
    """

    def __init__(self) -> None:
        """初始化 OpenAI 客户端。

        Raises:
            LLMClientError: API Key 未配置。
        """
        api_key = settings.openai_api_key.strip()
        if not api_key:
            raise LLMClientError("OPENAI_API_KEY 未配置")

        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url.rstrip("/"),
            timeout=settings.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def summarize(self, req: DigestSummaryInput) -> str:
        """生成日报摘要。"""
        system_prompt, user_prompt = _build_messages(req)

        try:
            response = await self._client.post(
                "/chat/completions",
                json={
                    "model": settings.openai_model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"OpenAI 请求超时: {exc}") from exc
        except httpx.RequestError as exc:
            raise LLMClientError(f"OpenAI 网络错误: {exc}") from exc

        if response.status_code >= 400:
            raise LLMClientError(f"OpenAI API 错误 [{response.status_code}]: {response.text}")

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMClientError(f"OpenAI 返回格式异常: {exc}") from exc

    async def aclose(self) -> None:
        """关闭客户端连接。"""
        await self._client.aclose()


# =============================================================================
# Anthropic 客户端
# =============================================================================


class AnthropicDigestClient:
    """Anthropic 日报摘要客户端。

    使用 httpx.AsyncClient 调用 Anthropic Messages API。

    Attributes:
        _client: HTTP 客户端实例。
    """

    def __init__(self) -> None:
        """初始化 Anthropic 客户端。

        Raises:
            LLMClientError: API Key 未配置。
        """
        api_key = settings.anthropic_api_key.strip()
        if not api_key:
            raise LLMClientError("ANTHROPIC_API_KEY 未配置")

        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            timeout=settings.llm_timeout_seconds,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    async def summarize(self, req: DigestSummaryInput) -> str:
        """生成日报摘要。"""
        system_prompt, user_prompt = _build_messages(req)

        try:
            response = await self._client.post(
                "/messages",
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 1500,
                    "temperature": 0.2,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
        except httpx.TimeoutException as exc:
            raise LLMClientError(f"Anthropic 请求超时: {exc}") from exc
        except httpx.RequestError as exc:
            raise LLMClientError(f"Anthropic 网络错误: {exc}") from exc

        if response.status_code >= 400:
            raise LLMClientError(f"Anthropic API 错误 [{response.status_code}]: {response.text}")

        try:
            data = response.json()
            blocks = data.get("content", [])
            text = "".join([b.get("text", "") for b in blocks if isinstance(b, dict)])
            return text.strip()
        except (KeyError, ValueError) as exc:
            raise LLMClientError(f"Anthropic 返回格式异常: {exc}") from exc

    async def aclose(self) -> None:
        """关闭客户端连接。"""
        await self._client.aclose()


# =============================================================================
# 工厂函数
# =============================================================================


def build_digest_llm_client() -> DigestLLMClient:
    """构建日报摘要 LLM 客户端。

    根据配置的 provider 返回对应的客户端实例。

    Returns:
        LLM 客户端实例。

    Raises:
        LLMClientError: Provider 不支持或配置缺失。
    """
    provider = settings.llm_provider.lower().strip()

    if provider == "anthropic":
        logger.info("使用 Anthropic LLM 客户端: model=%s", settings.anthropic_model)
        return AnthropicDigestClient()

    logger.info("使用 OpenAI LLM 客户端: model=%s", settings.openai_model)
    return OpenAIDigestClient()
