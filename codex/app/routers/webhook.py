"""Miniflux Webhook 路由"""
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.services.ingest import process_webhook_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(*, payload: bytes, signature: str, secret: str) -> bool:
    """验证 Miniflux webhook 签名"""
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/miniflux")
async def miniflux_webhook(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """
    接收 Miniflux webhook

    Miniflux 会在有新文章时发送 POST 请求到这里
    """
    settings = get_settings()

    # 读取原始 body
    body = await request.body()

    # 验证签名（如果配置了）
    if settings.miniflux_webhook_secret:
        signature = request.headers.get("X-Miniflux-Signature", "")
        if not verify_signature(
            payload=body,
            signature=signature,
            secret=settings.miniflux_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # 解析 JSON
    try:
        event = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 处理事件
    report = await process_webhook_event(db=db, event=event)

    return {
        "status": "ok",
        "report": report,
    }
