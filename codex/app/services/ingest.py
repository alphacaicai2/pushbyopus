"""入口编排服务"""
from collections.abc import Sequence

from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.dedup import filter_new_entries
from app.services.router import route_entries
from app.services.dispatch import dispatch_many_with_log


async def process_entries(*, db: AsyncSession, entries: Sequence[dict]) -> dict:
    """
    处理条目列表的完整流程

    流程: 去重 -> 路由 -> 发送（带日志）

    返回: {
        "received": int,
        "dedup_passed": int,
        "routed": int,
        "dispatch_attempted": int,
        "dispatch_succeeded": int,
        "dispatch_failed": int,
        "errors": list[str]
    }
    """
    report = {
        "received": len(entries),
        "dedup_passed": 0,
        "routed": 0,
        "dispatch_attempted": 0,
        "dispatch_succeeded": 0,
        "dispatch_failed": 0,
        "errors": [],
    }

    if not entries:
        return report

    # 1. 去重
    new_entries = await filter_new_entries(db=db, entries=entries)
    report["dedup_passed"] = len(new_entries)

    if not new_entries:
        return report

    # 2. 路由
    routed = await route_entries(db=db, entries=new_entries)
    report["routed"] = len(routed)

    if not routed:
        return report

    # 3. 发送（带日志记录）
    for entry, webhook_urls in routed:
        results = await dispatch_many_with_log(
            db=db,
            webhook_urls=webhook_urls,
            entry=entry,
        )

        report["dispatch_attempted"] += len(results)
        for r in results:
            if r["success"]:
                report["dispatch_succeeded"] += 1
            else:
                report["dispatch_failed"] += 1
                if r.get("error"):
                    report["errors"].append(r["error"])

    return report


async def process_webhook_event(*, db: AsyncSession, event: dict) -> dict:
    """
    处理 Miniflux webhook 事件

    事件格式:
    {
        "id": "event-id",
        "entries": [
            {
                "id": 123,
                "title": "...",
                "url": "...",
                "feed": {"id": 1, "title": "...", "category": {"id": 2, "title": "..."}},
            }
        ]
    }
    """
    entries = event.get("entries", [])
    return await process_entries(db=db, entries=entries)
