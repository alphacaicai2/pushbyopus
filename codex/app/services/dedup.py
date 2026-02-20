"""去重服务"""
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.seen_entry import SeenEntry


async def is_new_entry(*, db: AsyncSession, entry_url: str) -> bool:
    """检查是否为新条目"""
    statement = select(SeenEntry).where(SeenEntry.entry_url == entry_url)
    result = await db.execute(statement)
    return result.scalar_one_or_none() is None


async def mark_seen(*, db: AsyncSession, entry_url: str) -> None:
    """标记为已见"""
    entry = SeenEntry(entry_url=entry_url, seen_at=datetime.utcnow())
    db.add(entry)
    await db.commit()


async def filter_new_entries(
    *, db: AsyncSession, entries: Sequence[dict]
) -> list[dict]:
    """过滤出未见过的新条目"""
    new_entries = []
    for entry in entries:
        url = entry.get("url", "")
        if url and await is_new_entry(db=db, entry_url=url):
            new_entries.append(entry)
            await mark_seen(db=db, entry_url=url)
    return new_entries


async def cleanup_old_entries(*, db: AsyncSession, days: int = 7) -> int:
    """清理超过N天的去重记录"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    statement = select(SeenEntry).where(SeenEntry.seen_at < cutoff)
    result = await db.execute(statement)
    old_entries = result.scalars().all()

    for entry in old_entries:
        await db.delete(entry)

    await db.commit()
    return len(old_entries)
