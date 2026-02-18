"""Webhook API routes.

接收 Miniflux webhook 事件，分发给处理器。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.miniflux import MinifluxClientError
from app.webhook import WebhookHandler, WebhookPayload, verify_signature


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Webhook"])


@router.post(
    "/webhook/miniflux",
    summary="Receive Miniflux webhook",
    description="Receives Miniflux webhook events and pushes new entries to Discord in realtime.",
)
async def receive_miniflux_webhook(
    request: Request,
    x_miniflux_signature: str | None = Header(default=None, alias="X-Miniflux-Signature"),
    x_miniflux_event_type: str | None = Header(default=None, alias="X-Miniflux-Event-Type"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Miniflux webhook 接收入口。

    验证签名后，根据事件类型分发给对应的处理器。

    Headers:
        X-Miniflux-Signature: HMAC-SHA256 签名
        X-Miniflux-Event-Type: 事件类型 (new_entries, save_entry)

    Returns:
        处理结果摘要。
    """
    # 检查 secret 配置
    if not settings.miniflux_webhook_secret.strip():
        logger.error("miniflux_webhook_secret 未配置")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="miniflux_webhook_secret 未配置",
        )

    # 获取原始请求体
    raw_body = await request.body()

    # 验证签名
    if not verify_signature(raw_body, x_miniflux_signature, settings.miniflux_webhook_secret):
        logger.warning("Webhook 签名验证失败")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # 检查事件类型
    if not x_miniflux_event_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Miniflux-Event-Type header",
        )

    # 解析 JSON
    try:
        payload_data = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    # 验证 payload 结构
    try:
        payload = WebhookPayload.model_validate(payload_data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    # 处理事件
    handler = WebhookHandler(session)
    try:
        if x_miniflux_event_type == "new_entries":
            result = await handler.handle_new_entries(payload)
            logger.info(
                "Webhook 处理完成: event=%s total=%d pushed=%d failed=%d",
                x_miniflux_event_type,
                result.get("total", 0),
                result.get("pushed", 0),
                result.get("failed", 0),
            )
            return {
                "status": "ok",
                "event_type": x_miniflux_event_type,
                **result,
            }

        # 不支持的事件类型
        logger.info("忽略不支持的 webhook 事件: %s", x_miniflux_event_type)
        return {
            "status": "ignored",
            "event_type": x_miniflux_event_type,
            "reason": "unsupported_event",
        }

    except MinifluxClientError as exc:
        logger.error("Miniflux API 调用失败: %s", exc, exc_info=exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Miniflux request failed: {exc}",
        ) from exc
    finally:
        await handler.aclose()
