"""Scheduler module for managing periodic polling and digest jobs.

Provides an async scheduler using APScheduler for:
- Periodic RSS feed polling via Poller
- Scheduled digest generation via DigestService
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.database import AsyncSessionFactory
from app.models import PollSetting

logger = logging.getLogger(__name__)

# Job identifiers
POLL_JOB_ID = "poll_job"
DIGEST_JOB_ID = "digest_job"


async def poll_job() -> None:
    """Execute periodic polling task.

    Fetches new entries from all configured Miniflux categories
    and updates the last poll timestamp.
    """
    logger.info("Poll job started")

    async with AsyncSessionFactory() as session:
        try:
            from app.poller import Poller

            poller = Poller(session)
            results = await poller.poll_all_categories()
            logger.info(f"Poll completed: {results}")
        except Exception as exc:
            logger.exception(f"Poll job failed with error: {exc}")
            return

        # Update PollSetting.last_poll_at
        try:
            poll_setting = await session.get(PollSetting, 1)
            if poll_setting:
                poll_setting.last_poll_at = datetime.utcnow()
                await session.commit()
                logger.info(f"Updated last_poll_at to {poll_setting.last_poll_at}")
            else:
                logger.warning("PollSetting record not found (id=1)")
        except Exception as exc:
            logger.exception(f"Failed to update last_poll_at: {exc}")


async def digest_job() -> None:
    """Execute digest generation task.

    Generates and sends daily digest of RSS entries to Discord.
    """
    logger.info("Digest job started")

    async with AsyncSessionFactory() as session:
        try:
            from app.digest import DigestService

            service = DigestService(session)
            result = await service.run_digest_once()
            logger.info("Digest completed: %s", result)
        except Exception as exc:
            logger.exception("Digest job failed: %s", exc)


class Scheduler:
    """Manages scheduled jobs for polling and digest tasks.

    Attributes:
        scheduler: The underlying AsyncIOScheduler instance.
    """

    def __init__(self) -> None:
        """Initialize the scheduler with default configuration."""
        self._scheduler = AsyncIOScheduler()

    @property
    def scheduler(self) -> AsyncIOScheduler:
        """Get the underlying APScheduler instance."""
        return self._scheduler

    def start(self) -> None:
        """Start the scheduler if not already running."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started")
        else:
            logger.warning("Scheduler is already running")

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the scheduler gracefully.

        Args:
            wait: If True, wait for running jobs to complete.
        """
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("Scheduler shutdown complete")
        else:
            logger.info("Scheduler was not running")

    def add_poll_job(self, interval_minutes: int = 15) -> None:
        """Add or replace the periodic polling job.

        Args:
            interval_minutes: Polling interval in minutes (default: 15).
        """
        # Remove existing job if present (ignore if not found)
        try:
            self._scheduler.remove_job(POLL_JOB_ID)
        except Exception:
            pass

        trigger = IntervalTrigger(minutes=interval_minutes)
        self._scheduler.add_job(
            poll_job,
            trigger=trigger,
            id=POLL_JOB_ID,
            name="Periodic RSS Poll",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Poll job scheduled with interval: {interval_minutes} minutes")

    def add_digest_job(
        self,
        hour: int = 8,
        minute: int = 0,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        """Add or replace the daily digest job.

        Args:
            hour: Hour to run digest (0-23, default: 8).
            minute: Minute to run digest (0-59, default: 0).
            timezone_name: Timezone for scheduling (default: Asia/Shanghai).
        """
        # Remove existing job if present (ignore if not found)
        try:
            self._scheduler.remove_job(DIGEST_JOB_ID)
        except Exception:
            pass

        # Parse timezone
        original_timezone = timezone_name
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = ZoneInfo("UTC")
            timezone_name = "UTC"
            logger.warning("Invalid timezone '%s', falling back to UTC", original_timezone)

        trigger = CronTrigger(hour=hour, minute=minute, timezone=tz)
        self._scheduler.add_job(
            digest_job,
            trigger=trigger,
            id=DIGEST_JOB_ID,
            name="Daily Digest",
            replace_existing=True,
            max_instances=1,
        )
        logger.info("Digest job scheduled daily at %02d:%02d (%s)", hour, minute, timezone_name)

    def update_poll_interval(self, interval_minutes: int) -> None:
        """Update the polling interval by replacing the existing job.

        Args:
            interval_minutes: New polling interval in minutes.
        """
        logger.info(f"Updating poll interval to {interval_minutes} minutes")
        self.add_poll_job(interval_minutes=interval_minutes)

    def trigger_poll_now(self) -> None:
        """Trigger an immediate poll execution.

        The job runs asynchronously; this method returns immediately.
        """
        if self._scheduler.get_job(POLL_JOB_ID):
            self._scheduler.modify_job(POLL_JOB_ID, next_run_time=datetime.now())
            logger.info("Poll job triggered for immediate execution")
        else:
            logger.warning("Cannot trigger poll: poll job not scheduled")


# Global scheduler instance
scheduler = Scheduler()
