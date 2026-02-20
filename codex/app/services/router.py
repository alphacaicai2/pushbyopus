"""路由服务 - 简化版：根据分类ID查找webhook"""
from collections.abc import Sequence

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.route_rule import RouteRule, RouteRuleCreate, RouteRuleUpdate


async def resolve_webhooks(*, db: AsyncSession, entry: dict) -> list[str]:
    """
    根据条目的分类ID解析对应的 webhook 列表

    一个分类可以对应多个 webhook，返回所有匹配的 webhook URL
    """
    # 从 entry 中提取 category ID
    # Miniflux webhook 格式: entry.feed.category.id 或 entry.category.id
    category_id = None

    # 尝试从 feed.category 获取
    feed_obj = entry.get("feed", {}) or {}
    category_obj = feed_obj.get("category", {}) or {}

    if category_obj.get("id"):
        category_id = category_obj["id"]
    elif entry.get("category", {}).get("id"):
        # 直接从 entry.category 获取
        category_id = entry["category"]["id"]

    if category_id is None:
        return []

    # 查询该分类对应的所有启用的 webhook
    statement = (
        select(RouteRule)
        .where(RouteRule.category_id == category_id)
        .where(RouteRule.enabled == True)
    )
    result = await db.execute(statement)
    rules = result.scalars().all()

    return [rule.webhook_url for rule in rules]


async def route_entries(
    *, db: AsyncSession, entries: Sequence[dict]
) -> list[tuple[dict, list[str]]]:
    """批量路由条目"""
    results = []
    for entry in entries:
        webhooks = await resolve_webhooks(db=db, entry=entry)
        if webhooks:
            results.append((entry, webhooks))
    return results


# CRUD 操作

async def list_route_rules(
    *, db: AsyncSession, enabled_only: bool = False
) -> list[RouteRule]:
    """列出所有路由规则"""
    statement = select(RouteRule).order_by(RouteRule.category_id)
    if enabled_only:
        statement = statement.where(RouteRule.enabled == True)
    result = await db.execute(statement)
    return list(result.scalars().all())


async def get_rules_by_category(*, db: AsyncSession, category_id: int) -> list[RouteRule]:
    """获取某个分类的所有规则"""
    statement = (
        select(RouteRule)
        .where(RouteRule.category_id == category_id)
        .order_by(RouteRule.id)
    )
    result = await db.execute(statement)
    return list(result.scalars().all())


async def create_route_rule(*, db: AsyncSession, data: RouteRuleCreate) -> RouteRule:
    """创建路由规则"""
    rule = RouteRule(**data.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def update_route_rule(
    *, db: AsyncSession, rule_id: int, data: RouteRuleUpdate
) -> RouteRule | None:
    """更新路由规则"""
    statement = select(RouteRule).where(RouteRule.id == rule_id)
    result = await db.execute(statement)
    rule = result.scalar_one_or_none()

    if rule is None:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rule, key, value)

    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_route_rule(*, db: AsyncSession, rule_id: int) -> bool:
    """删除路由规则"""
    statement = select(RouteRule).where(RouteRule.id == rule_id)
    result = await db.execute(statement)
    rule = result.scalar_one_or_none()

    if rule is None:
        return False

    await db.delete(rule)
    await db.commit()
    return True


async def delete_rules_by_category(*, db: AsyncSession, category_id: int) -> int:
    """删除某个分类的所有规则"""
    rules = await get_rules_by_category(db=db, category_id=category_id)
    count = 0
    for rule in rules:
        await db.delete(rule)
        count += 1
    await db.commit()
    return count
