"""Discord 发送服务（带日志记录）"""
import asyncio
from collections.abc import Sequence

import httpx

from app.config import get_settings
from app.models.push_log import PushLog


def build_discord_payload(*, entry: dict) -> dict:
    """
    构建 Discord webhook payload

    使用 content 字段（最稳定）
    """
    title = entry.get("title", "无标题")
    url = entry.get("url", "")

    if url:
        content = f"📰 [{title}]({url})"
    else:
        content = f"📰 {title}"

    # 添加来源信息
    feed_title = entry.get("feed", {}).get("title")
    if feed_title:
        content += f"\n_来源: {feed_title}_"

    return {"content": content}


async def dispatch_one(
    *,
    webhook_url: str,
    payload: dict,
    timeout_s: float = 10.0,
    max_retries: int = 3,
) -> dict:
    """
    发送到单个 webhook

    返回: {
        "webhook_url": str,
        "success": bool,
        "status_code": int | None,
        "retryable": bool,
        "error": str | None
    }
    """
    settings = get_settings()
    timeout_s = timeout_s or settings.discord_timeout_s
    max_retries = max_retries or settings.discord_max_retries

    last_error = None
    last_status = None

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.post(webhook_url, json=payload)
                last_status = response.status_code

                if response.status_code == 204:
                    return {
                        "webhook_url": webhook_url,
                        "success": True,
                        "status_code": 204,
                        "retryable": False,
                        "error": None,
                    }

                if response.status_code == 429:
                    # Rate limited - 获取等待时间
                    retry_after = response.headers.get("Retry-After", "5")
                    wait_time = float(retry_after)
                    await asyncio.sleep(wait_time)
                    continue

                if 200 <= response.status_code < 300:
                    return {
                        "webhook_url": webhook_url,
                        "success": True,
                        "status_code": response.status_code,
                        "retryable": False,
                        "error": None,
                    }

                if response.status_code >= 500:
                    # 服务器错误，重试
                    last_error = f"Server error: {response.status_code}"
                    await asyncio.sleep(2 ** attempt)
                    continue

                # 其他 4xx 错误，不重试
                return {
                    "webhook_url": webhook_url,
                    "success": False,
                    "status_code": response.status_code,
                    "retryable": False,
                    "error": f"HTTP {response.status_code}: {response.text[:200]}",
                }

        except httpx.TimeoutException:
            last_error = "Timeout"
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(2 ** attempt)

    return {
        "webhook_url": webhook_url,
        "success": False,
        "status_code": last_status,
        "retryable": True,
        "error": last_error,
    }


async def dispatch_with_log(
    *,
    db,
    webhook_url: str,
    entry: dict,
    timeout_s: float = 10.0,
    max_retries: int = 3,
) -> dict:
    """
    发送到单个 webhook 并记录日志
    """
    payload = build_discord_payload(entry=entry)
    result = await dispatch_one(
        webhook_url=webhook_url,
        payload=payload,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )

    # 提取分类信息
    feed_obj = entry.get("feed", {}) or {}
    category_obj = feed_obj.get("category", {}) or {}
    category_id = category_obj.get("id")
    category_name = category_obj.get("title", "")

    # 记录日志
    log = PushLog(
        entry_title=entry.get("title", "无标题")[:200],
        entry_url=entry.get("url", "")[:500],
        category_name=category_name,
        category_id=category_id,
        webhook_url=webhook_url[:200],
        success=result["success"],
        error_message=result.get("error", "")[:500] if result.get("error") else None,
    )
    db.add(log)
    await db.commit()

    return result


async def dispatch_many(
    *,
    webhook_urls: Sequence[str],
    payload: dict,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    concurrency: int = 4,
) -> list[dict]:
    """
    并发发送到多个 webhook

    使用信号量控制并发数
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def send_with_limit(url: str) -> dict:
        async with semaphore:
            await asyncio.sleep(0.1)  # 轻微延迟，避免突发
            return await dispatch_one(
                webhook_url=url,
                payload=payload,
                timeout_s=timeout_s,
                max_retries=max_retries,
            )

    tasks = [send_with_limit(url) for url in webhook_urls]
    return await asyncio.gather(*tasks)


async def dispatch_many_with_log(
    *,
    db,
    webhook_urls: Sequence[str],
    entry: dict,
    timeout_s: float = 10.0,
    max_retries: int = 3,
    concurrency: int = 4,
) -> list[dict]:
    """
    并发发送到多个 webhook 并记录日志
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def send_with_limit(url: str) -> dict:
        async with semaphore:
            await asyncio.sleep(0.1)
            return await dispatch_with_log(
                db=db,
                webhook_url=url,
                entry=entry,
                timeout_s=timeout_s,
                max_retries=max_retries,
            )

    tasks = [send_with_limit(url) for url in webhook_urls]
    return await asyncio.gather(*tasks)
