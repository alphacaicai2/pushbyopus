"""
FastAPI application entry point.

Miniflux -> Discord Push Service

This service provides:
- Web UI for configuring RSS-to-Discord mappings
- RESTful API for configuration management
- Scheduled polling of Miniflux RSS feeds
- Discord webhook push notifications
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import init_db, get_db
from app.models import CategoryBinding, DigestSetting, PollSetting, DeliveryLog
from app.routers.config import router as config_router
from app.scheduler import scheduler


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_hhmm(value: str | None) -> tuple[int, int]:
    """解析 HH:MM 格式的时间字符串。"""
    raw = (value or "08:00").strip()
    try:
        hour_str, minute_str = raw.split(":", 1)
        hour, minute = int(hour_str), int(minute_str)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except Exception:
        pass
    return 8, 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup and shutdown events."""
    logger.info("Starting up Miniflux -> Discord Push Service...")
    await init_db()
    logger.info("Database initialized successfully")

    # Get settings from database
    async for session in get_db():
        poll_setting = await session.get(PollSetting, 1)
        digest_setting = await session.get(DigestSetting, 1)

        poll_interval = poll_setting.interval_minutes if poll_setting else 15
        digest_time = digest_setting.digest_time if digest_setting else "08:00"
        digest_tz = digest_setting.timezone if digest_setting else "Asia/Shanghai"
        break

    # Start scheduler with poll and digest jobs
    scheduler.add_poll_job(interval_minutes=poll_interval)

    hour, minute = _parse_hhmm(digest_time)
    scheduler.add_digest_job(hour=hour, minute=minute, timezone_name=digest_tz)

    scheduler.start()
    logger.info(
        "Scheduler started: poll=%d min, digest=%02d:%02d (%s)",
        poll_interval,
        hour,
        minute,
        digest_tz,
    )

    yield

    # Shutdown scheduler
    scheduler.shutdown(wait=True)
    logger.info("Shutting down Miniflux -> Discord Push Service...")


app = FastAPI(
    title="Miniflux -> Discord Push Service",
    description="Push RSS updates from Miniflux to Discord channels",
    version="0.1.0",
    lifespan=lifespan,
)

# Mount static files and configure templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Include API routers
app.include_router(config_router)


@app.get("/", response_class=HTMLResponse, summary="Configuration UI")
async def index(request: Request, message: str = "", type: str = "") -> HTMLResponse:
    """Render the main configuration page with all context data."""
    # Get database session
    async for session in get_db():
        # Get or create poll setting
        poll_setting = await session.get(PollSetting, 1)
        if poll_setting is None:
            poll_setting = PollSetting(id=1)
            session.add(poll_setting)
            await session.commit()
            await session.refresh(poll_setting)

        # Get or create digest setting
        digest_setting = await session.get(DigestSetting, 1)
        if digest_setting is None:
            digest_setting = DigestSetting(id=1)
            session.add(digest_setting)
            await session.commit()
            await session.refresh(digest_setting)

        # Get all bindings
        bindings_result = await session.execute(
            select(CategoryBinding).order_by(CategoryBinding.category_id.asc())
        )
        bindings = list(bindings_result.scalars().all())

        # Get recent logs
        logs_result = await session.execute(
            select(DeliveryLog)
            .order_by(DeliveryLog.pushed_at.desc())
            .limit(10)
        )
        logs = list(logs_result.scalars().all())

        # Get display timezone and convert log timestamps
        display_tz_name = poll_setting.display_timezone if poll_setting and poll_setting.display_timezone else "Asia/Shanghai"
        try:
            display_tz = ZoneInfo(display_tz_name)
        except Exception:
            display_tz = ZoneInfo("Asia/Shanghai")
            display_tz_name = "Asia/Shanghai"

        # Convert log timestamps to display timezone
        formatted_logs = []
        for log in logs:
            if log.pushed_at:
                # Assume stored time is UTC if no timezone info
                dt_utc = log.pushed_at
                if dt_utc.tzinfo is None:
                    dt_utc = dt_utc.replace(tzinfo=timezone.utc)
                dt_local = dt_utc.astimezone(display_tz)
                formatted_time = dt_local.strftime("%Y-%m-%d %H:%M:%S") + f" ({display_tz_name})"
            else:
                formatted_time = None
            formatted_logs.append({
                "pushed_at": formatted_time,
                "category_name": log.category_name,
                "title": log.title,
                "status": log.status,
            })

        # Build status
        status = {
            "running": True,
            "last_poll_at": poll_setting.last_poll_at.strftime("%Y-%m-%d %H:%M:%S") if poll_setting.last_poll_at else None,
        }

        return templates.TemplateResponse("index.html", {
            "request": request,
            "status": status,
            "poll_setting": poll_setting,
            "digest_setting": digest_setting,
            "bindings": bindings,
            "logs": formatted_logs,
            "message": message,
            "message_type": type or "success",
        })
