"""
Configuration API routes.

This module provides endpoints for managing:
- Miniflux category synchronization
- Category-to-Discord webhook bindings
- Digest settings (daily summary configuration)
- Poll settings (RSS fetch interval)
- Test push functionality
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.database import get_db
from app.discord import (
    DiscordWebhookAPIError,
    DiscordWebhookClient,
    DiscordWebhookClientError,
    DiscordWebhookNetworkError,
    sanitize_webhook_url,
)
from app.miniflux import get_categories as fetch_miniflux_categories
from app.models import CategoryBinding, DigestSetting, PollSetting
from app.schemas import (
    CategoryBindingResponse,
    CategoryBindingUpdate,
    DigestSettingResponse,
    DigestSettingUpdate,
    PollSettingResponse,
    PollSettingUpdate,
    TestPushResponse,
)
from app.scheduler import scheduler


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Configuration"])


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


async def _get_or_create_digest_setting(session: AsyncSession) -> DigestSetting:
    """Get or create the singleton digest setting record (id=1)."""
    setting = await session.get(DigestSetting, 1)
    if setting is None:
        setting = DigestSetting(id=1)
        session.add(setting)
        await session.commit()
        await session.refresh(setting)
    return setting


async def _get_or_create_poll_setting(session: AsyncSession) -> PollSetting:
    """Get or create the singleton poll setting record (id=1)."""
    setting = await session.get(PollSetting, 1)
    if setting is None:
        setting = PollSetting(id=1)
        session.add(setting)
        await session.commit()
        await session.refresh(setting)
    return setting


@router.get(
    "/categories",
    response_model=list[CategoryBindingResponse],
    summary="Sync categories from Miniflux",
    description="Fetches all categories from Miniflux and synchronizes them with local database. "
    "New categories are added, existing ones have their names updated if changed.",
)
async def sync_categories(
    session: AsyncSession = Depends(get_db),
) -> list[CategoryBinding]:
    """
    Sync categories from Miniflux to local database.

    - Fetches all categories from Miniflux API
    - Creates new local bindings for unknown categories
    - Updates category names if they changed in Miniflux
    - Preserves existing webhook_url and enabled flags

    Returns 502 Bad Gateway if Miniflux is unreachable.
    """
    try:
        categories = await fetch_miniflux_categories()
    except Exception as exc:
        logger.error(f"Failed to fetch categories from Miniflux: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch categories from Miniflux: {exc}",
        ) from exc

    # Load existing bindings into a dict for quick lookup
    result = await session.execute(select(CategoryBinding))
    existing: dict[int, CategoryBinding] = {
        row.category_id: row for row in result.scalars().all()
    }

    # Process each category from Miniflux
    for item in categories:
        raw_id = item.get("id")
        if raw_id is None:
            continue

        try:
            category_id = int(raw_id)
        except (TypeError, ValueError):
            logger.warning(f"Invalid category ID from Miniflux: {raw_id}")
            continue

        # Miniflux may return 'title' or 'name' depending on version
        category_name = item.get("title") or item.get("name") or f"Category {category_id}"

        if category_id in existing:
            # Update name if changed
            row = existing[category_id]
            if row.category_name != category_name:
                row.category_name = category_name
                logger.info(f"Updated category {category_id} name to: {category_name}")
        else:
            # Create new binding with defaults
            session.add(
                CategoryBinding(
                    category_id=category_id,
                    category_name=category_name,
                )
            )
            logger.info(f"Created new category binding: {category_id} - {category_name}")

    await session.commit()

    # Return all bindings sorted by category_id
    synced = await session.execute(
        select(CategoryBinding).order_by(CategoryBinding.category_id.asc())
    )
    return list(synced.scalars().all())


@router.get(
    "/bindings",
    response_model=list[CategoryBindingResponse],
    summary="Get all category bindings",
    description="Returns all category bindings with their Discord webhook configurations.",
)
async def get_bindings(
    session: AsyncSession = Depends(get_db),
) -> list[CategoryBinding]:
    """Get all category bindings sorted by category ID."""
    result = await session.execute(
        select(CategoryBinding).order_by(CategoryBinding.category_id.asc())
    )
    return list(result.scalars().all())


@router.put(
    "/bindings/{category_id}",
    response_model=CategoryBindingResponse,
    summary="Update a category binding",
    description="Updates the Discord webhook configuration for a specific category. "
    "Returns 404 if the category binding does not exist.",
)
async def update_binding(
    category_id: int,
    payload: CategoryBindingUpdate,
    session: AsyncSession = Depends(get_db),
) -> CategoryBinding:
    """
    Update a category binding.

    Only provided fields will be updated (partial update semantics).
    Raises 404 if no binding exists for the given category_id.
    """
    result = await session.execute(
        select(CategoryBinding).where(CategoryBinding.category_id == category_id)
    )
    binding = result.scalar_one_or_none()

    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category binding not found: {category_id}",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(binding, field, value)

    await session.commit()
    await session.refresh(binding)

    logger.info(f"Updated binding for category {category_id}: {update_data}")
    return binding


@router.get(
    "/test-push/{category_id}",
    response_model=TestPushResponse,
    summary="Test push for a category",
    description="Sends a single test message to the category's configured Discord webhook "
    "to verify the configuration is working correctly.",
    responses={
        200: {"description": "Test message sent successfully"},
        400: {"description": "Webhook URL not configured or invalid"},
        404: {"description": "Category binding not found"},
        502: {"description": "Discord API or network error"},
        500: {"description": "Internal server error"},
    },
)
async def test_push_for_category(
    category_id: int,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> TestPushResponse:
    """
    Send a test Discord message for a specific category binding.

    This endpoint validates the webhook configuration by sending a test message
    to Discord. It performs the following checks:
    - Verifies the category binding exists
    - Ensures webhook_url is configured
    - Sends a test message via Discord Webhook API
    - Returns detailed success/failure information

    The test message includes:
    - Category name in the title
    - System test indicator
    - Current timestamp

    Error codes returned:
    - CATEGORY_NOT_FOUND: The category binding does not exist
    - WEBHOOK_NOT_CONFIGURED: webhook_url is empty or not set
    - INVALID_WEBHOOK_URL: webhook_url format is invalid
    - DISCORD_NETWORK_ERROR: Network failure when contacting Discord
    - DISCORD_API_ERROR: Discord API returned an error
    - DISCORD_CLIENT_ERROR: Other Discord client errors
    - INTERNAL_ERROR: Unexpected internal error
    """
    sent_at = datetime.now(timezone.utc)

    # Query the category binding
    result = await session.execute(
        select(CategoryBinding).where(CategoryBinding.category_id == category_id)
    )
    binding = result.scalar_one_or_none()

    # Check if binding exists
    if binding is None:
        logger.warning(f"Test push failed: category binding not found: {category_id}")
        response.status_code = status.HTTP_404_NOT_FOUND
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=None,
            message="分类不存在，无法发送测试消息",
            webhook_url_masked=None,
            sent_at=sent_at,
            error_code="CATEGORY_NOT_FOUND",
            detail=f"Category binding not found: {category_id}",
        )

    # Get and validate webhook URL
    webhook_url = (binding.webhook_url or "").strip()
    masked_webhook_url = sanitize_webhook_url(webhook_url)

    if not webhook_url:
        logger.warning(
            f"Test push failed: webhook_url not configured for category {category_id}"
        )
        response.status_code = status.HTTP_400_BAD_REQUEST
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="该分类未配置 Webhook URL，请先在配置页面设置",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="WEBHOOK_NOT_CONFIGURED",
            detail="webhook_url is empty or not configured",
        )

    # Construct simple test entry
    test_entry: dict[str, Any] = {
        "title": "测试消息",
        "summary": "本消息是测试消息",
        "author": None,
        "feed": {"title": "System Test"},
        "published_at": None,
    }

    # Get display timezone from settings
    poll_setting = await session.get(PollSetting, 1)
    display_tz = poll_setting.display_timezone if poll_setting else "Asia/Shanghai"

    logger.info(
        f"Starting test push: category_id={category_id}, "
        f"category_name={binding.category_name}, "
        f"webhook_url={masked_webhook_url}"
    )

    # Attempt to send test message
    try:
        async with DiscordWebhookClient() as client:
            await client.send_entry(
                webhook_url=webhook_url,
                entry=test_entry,
                category_name=binding.category_name,
                display_timezone=display_tz,
            )
    except asyncio.CancelledError:
        # Re-raise cancellation to allow proper task cleanup
        logger.info(f"Test push cancelled for category {category_id}")
        raise
    except DiscordWebhookNetworkError as exc:
        # Network-level error (timeout, DNS, connection refused, etc.)
        response.status_code = status.HTTP_502_BAD_GATEWAY
        logger.error(
            f"Test push network error for category {category_id}: {exc}",
            exc_info=True,
        )
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="测试消息发送失败：网络连接错误",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="DISCORD_NETWORK_ERROR",
            detail="Network error when connecting to Discord",
        )
    except DiscordWebhookAPIError as exc:
        # Discord API returned an error response
        # Map 4xx to 400 (client error), 5xx to 502 (upstream error)
        if 400 <= exc.status_code < 500:
            response.status_code = status.HTTP_400_BAD_REQUEST
        else:
            response.status_code = status.HTTP_502_BAD_GATEWAY
        logger.error(
            f"Test push API error for category {category_id}: "
            f"status={exc.status_code}, error={exc}",
            exc_info=True,
        )
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="测试消息发送失败：Discord API 返回错误",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="DISCORD_API_ERROR",
            detail=f"Discord API returned HTTP {exc.status_code}",
        )
    except ValueError as exc:
        # Invalid webhook URL format
        response.status_code = status.HTTP_400_BAD_REQUEST
        logger.error(
            f"Test push invalid webhook URL for category {category_id}: {exc}"
        )
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="测试消息发送失败：Webhook URL 格式无效",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="INVALID_WEBHOOK_URL",
            detail="The webhook URL format is invalid",
        )
    except DiscordWebhookClientError as exc:
        # Other Discord client errors
        response.status_code = status.HTTP_502_BAD_GATEWAY
        logger.error(
            f"Test push client error for category {category_id}: {exc}",
            exc_info=True,
        )
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="测试消息发送失败：Discord 客户端错误",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="DISCORD_CLIENT_ERROR",
            detail="Discord webhook client error",
        )
    except Exception as exc:
        # Catch-all for unexpected errors
        response.status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        logger.exception(
            f"Test push unexpected error for category {category_id}: {exc}"
        )
        return TestPushResponse(
            status="error",
            category_id=category_id,
            category_name=binding.category_name,
            message="测试消息发送失败：内部服务器错误",
            webhook_url_masked=masked_webhook_url,
            sent_at=sent_at,
            error_code="INTERNAL_ERROR",
            detail="An unexpected internal error occurred",
        )

    # Success case
    logger.info(f"Test push successful for category {category_id}")
    return TestPushResponse(
        status="success",
        category_id=category_id,
        category_name=binding.category_name,
        message="测试消息发送成功，请检查 Discord 频道",
        webhook_url_masked=masked_webhook_url,
        sent_at=sent_at,
        error_code=None,
        detail=None,
    )


@router.get(
    "/digest-settings",
    response_model=DigestSettingResponse,
    summary="Get digest settings",
)
async def get_digest_settings(
    session: AsyncSession = Depends(get_db),
) -> DigestSetting:
    """Get current digest settings. Creates default if not exists."""
    return await _get_or_create_digest_setting(session)


@router.put(
    "/digest-settings",
    response_model=DigestSettingResponse,
    summary="Update digest settings",
)
async def update_digest_settings(
    payload: DigestSettingUpdate,
    session: AsyncSession = Depends(get_db),
) -> DigestSetting:
    """Update digest settings. Only provided fields will be updated."""
    setting = await _get_or_create_digest_setting(session)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(setting, field, value)

    await session.commit()
    await session.refresh(setting)

    # Reload scheduler with new digest time
    hour, minute = _parse_hhmm(setting.digest_time)
    timezone_name = getattr(setting, "timezone", "Asia/Shanghai")
    scheduler.add_digest_job(hour=hour, minute=minute, timezone_name=timezone_name)
    logger.info("Digest scheduler updated: %02d:%02d (%s)", hour, minute, timezone_name)

    return setting


@router.get(
    "/poll-settings",
    response_model=PollSettingResponse,
    summary="Get poll settings",
)
async def get_poll_settings(
    session: AsyncSession = Depends(get_db),
) -> PollSetting:
    """Get current poll settings. Creates default if not exists."""
    return await _get_or_create_poll_setting(session)


@router.put(
    "/poll-settings",
    response_model=PollSettingResponse,
    summary="Update poll settings",
)
async def update_poll_settings(
    payload: PollSettingUpdate,
    session: AsyncSession = Depends(get_db),
) -> PollSetting:
    """Update poll settings. Only provided fields will be updated."""
    setting = await _get_or_create_poll_setting(session)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(setting, field, value)

    await session.commit()
    await session.refresh(setting)

    # Reload scheduler with new poll interval
    scheduler.update_poll_interval(setting.interval_minutes)
    logger.info("Poll scheduler updated: interval=%d min", setting.interval_minutes)

    return setting


@router.post(
    "/poll/now",
    summary="Trigger immediate poll",
    description="Triggers an immediate poll of Miniflux for new entries.",
)
async def trigger_poll_now(
    session: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Trigger an immediate poll.

    Returns a status message indicating the poll was triggered.
    """
    from app.poller import Poller

    logger.info("Manual poll triggered via POST")
    try:
        async with Poller(session) as poller:
            result = await poller.poll_all_categories()
        logger.info(f"Poll completed: {result}")
        return {"status": "success", "message": f"轮询完成: {result}"}
    except Exception as exc:
        logger.exception(f"Poll failed: {exc}")
        return {"status": "error", "message": f"轮询失败: {exc}"}


@router.get(
    "/poll/now",
    summary="Trigger immediate poll (redirect)",
)
async def trigger_poll_now_redirect(
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Trigger immediate poll and redirect back to home."""
    from app.poller import Poller

    logger.info("Manual poll triggered via GET")
    try:
        async with Poller(session) as poller:
            result = await poller.poll_all_categories()
        logger.info(f"Poll completed: {result}")
        return RedirectResponse(
            url=f"/?message=轮询完成: 推送 {sum(result.values())} 条&type=success",
            status_code=303
        )
    except Exception as exc:
        logger.exception(f"Poll failed: {exc}")
        return RedirectResponse(
            url=f"/?message=轮询失败: {exc}&type=error",
            status_code=303
        )


@router.get(
    "/sync-categories",
    summary="Sync categories and redirect",
)
async def sync_categories_redirect(
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Sync categories from Miniflux and redirect back to home."""
    try:
        categories = await fetch_miniflux_categories()
    except Exception as exc:
        logger.error(f"Failed to fetch categories from Miniflux: {exc}")
        return RedirectResponse(
            url=f"/?message=同步失败: {exc}&type=error",
            status_code=303
        )

    # Load existing bindings into a dict for quick lookup
    result = await session.execute(select(CategoryBinding))
    existing: dict[int, CategoryBinding] = {
        row.category_id: row for row in result.scalars().all()
    }

    # Process each category from Miniflux
    for item in categories:
        raw_id = item.get("id")
        if raw_id is None:
            continue

        try:
            category_id = int(raw_id)
        except (TypeError, ValueError):
            logger.warning(f"Invalid category ID from Miniflux: {raw_id}")
            continue

        category_name = item.get("title") or item.get("name") or f"Category {category_id}"

        if category_id in existing:
            row = existing[category_id]
            if row.category_name != category_name:
                row.category_name = category_name
                logger.info(f"Updated category {category_id} name to: {category_name}")
        else:
            session.add(
                CategoryBinding(
                    category_id=category_id,
                    category_name=category_name,
                )
            )
            logger.info(f"Created new category binding: {category_id} - {category_name}")

    await session.commit()
    return RedirectResponse(
        url="/?message=分组同步成功&type=success",
        status_code=303
    )


@router.post(
    "/config/save",
    summary="Save all configuration",
)
async def save_config(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Save all configuration from the form."""
    form = await request.form()

    # Update poll settings
    poll_setting = await _get_or_create_poll_setting(session)
    if "miniflux_url" in form:
        poll_setting.miniflux_url = form["miniflux_url"]
    if "miniflux_token" in form and form["miniflux_token"]:
        poll_setting.miniflux_token = form["miniflux_token"]
    if "interval_minutes" in form:
        poll_setting.interval_minutes = int(form["interval_minutes"])
    if "display_timezone" in form:
        poll_setting.display_timezone = form["display_timezone"]

    # Update digest settings
    digest_setting = await _get_or_create_digest_setting(session)
    if "digest_time" in form:
        digest_setting.digest_time = form["digest_time"]
    if "period_hours" in form:
        digest_setting.period_hours = int(form["period_hours"])
    if "summary_length" in form:
        digest_setting.summary_length = form["summary_length"]
    if "language_mode" in form:
        digest_setting.language_mode = form["language_mode"]
    if "digest_timezone" in form:
        digest_setting.timezone = form["digest_timezone"]

    # Update bindings
    # Parse bindings from form data
    bindings_data: dict[int, dict] = {}
    for key, value in form.items():
        if key.startswith("bindings-"):
            parts = key.split("-", 2)
            if len(parts) >= 3:
                try:
                    category_id = int(parts[1])
                    field_name = parts[2]
                    if category_id not in bindings_data:
                        bindings_data[category_id] = {}
                    bindings_data[category_id][field_name] = value
                except ValueError:
                    continue

    # Update each binding
    for category_id, data in bindings_data.items():
        result = await session.execute(
            select(CategoryBinding).where(CategoryBinding.category_id == category_id)
        )
        binding = result.scalar_one_or_none()
        if binding:
            if "webhook_url" in data:
                binding.webhook_url = data["webhook_url"] or None
            binding.realtime_enabled = f"bindings-{category_id}-realtime_enabled" in form
            binding.digest_enabled = f"bindings-{category_id}-digest_enabled" in form

    await session.commit()
    logger.info("Configuration saved successfully")

    # Reload scheduler with new settings
    scheduler.update_poll_interval(poll_setting.interval_minutes)
    hour, minute = _parse_hhmm(digest_setting.digest_time)
    timezone_name = getattr(digest_setting, "timezone", "Asia/Shanghai")
    scheduler.add_digest_job(hour=hour, minute=minute, timezone_name=timezone_name)
    logger.info("Scheduler reloaded: poll=%d min, digest=%02d:%02d (%s)",
                poll_setting.interval_minutes, hour, minute, timezone_name)

    return RedirectResponse(
        url="/?message=配置保存成功&type=success",
        status_code=303
    )
