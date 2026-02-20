"""Miniflux 客户端 - 同步 feeds 和 categories"""
import httpx

from app.config import get_settings


async def get_feeds() -> list[dict]:
    """从 Miniflux 获取所有 feeds"""
    settings = get_settings()
    if not settings.miniflux_url or not settings.miniflux_token:
        return []

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{settings.miniflux_url.rstrip('/')}/feeds",
                headers={"X-Auth-Token": settings.miniflux_token},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching feeds: {e}")
            return []


async def get_categories() -> list[dict]:
    """从 Miniflux 获取所有 categories"""
    settings = get_settings()
    if not settings.miniflux_url or not settings.miniflux_token:
        return []

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{settings.miniflux_url.rstrip('/')}/categories",
                headers={"X-Auth-Token": settings.miniflux_token},
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error fetching categories: {e}")
            return []


async def get_feeds_with_categories() -> list[dict]:
    """获取 feeds 并附带 category 信息"""
    feeds = await get_feeds()
    categories = await get_categories()

    # 创建 category id -> name 映射
    cat_map = {c["id"]: c["title"] for c in categories}

    # 给每个 feed 添加 category_name
    for feed in feeds:
        cat_id = feed.get("category", {}).get("id")
        feed["category_name"] = cat_map.get(cat_id, "未分类")

    return feeds
