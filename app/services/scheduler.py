"""APScheduler setup: incremental scans, TMDB sync, and health pings."""
from __future__ import annotations

import asyncio
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models.catalog import CatalogItem
from app.services import catalog_service
from app.services.scanner import TelegramScanner
from app.services.tmdb import TMDBService
from app.utils.logger import get_logger
from sqlalchemy import select

logger = get_logger(__name__)


class SchedulerService:
    INCREMENTAL_SCAN_JOB = "INCREMENTAL_SCAN"
    TMDB_SYNC_JOB = "TMDB_SYNC"
    HEALTH_PING_JOB = "HEALTH_PING"

    def __init__(
        self,
        scanner: TelegramScanner,
        tmdb_service: TMDBService,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.scanner = scanner
        self.tmdb = tmdb_service
        self.scheduler = AsyncIOScheduler(timezone="UTC")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.scheduler.add_job(
            self._job_incremental_scan,
            trigger=IntervalTrigger(hours=int(self.settings.SCAN_INTERVAL_HOURS)),
            id=self.INCREMENTAL_SCAN_JOB,
            name="Incremental Telegram channel scan",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._job_tmdb_sync,
            trigger=IntervalTrigger(days=int(self.settings.TMDB_SYNC_INTERVAL_DAYS)),
            id=self.TMDB_SYNC_JOB,
            name="TMDB metadata re-sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._job_health_ping,
            trigger=IntervalTrigger(minutes=5),
            id=self.HEALTH_PING_JOB,
            name="Scanner health ping",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )
        self.scheduler.start()
        logger.info(
            "APScheduler started",
            extra={
                "scan_interval_hours": self.settings.SCAN_INTERVAL_HOURS,
                "tmdb_sync_interval_days": self.settings.TMDB_SYNC_INTERVAL_DAYS,
            },
        )

        # Startup behavior: first-ever run triggers a full historical scan
        # immediately (in the background); later runs resume incrementally.
        asyncio.create_task(self._startup_check())

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped")

    # ------------------------------------------------------------------
    # Startup behavior
    # ------------------------------------------------------------------

    async def _startup_check(self) -> None:
        await asyncio.sleep(10)  # let the API finish booting first
        if not self.scanner.is_configured:
            logger.info("Telegram not configured; skipping startup scan check")
            return
        try:
            factory = get_session_factory()
            async with factory() as session:
                empty = await catalog_service.scan_tracker_empty(session)
            if empty:
                logger.info(
                    "First startup detected (scan_tracker empty); "
                    "triggering full historical scan in background"
                )
                await self._run_scan(incremental=False)
            else:
                logger.info("Resuming incremental scanning on schedule")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Startup scan check failed", extra={"error": str(exc)})

    # ------------------------------------------------------------------
    # Job implementations
    # ------------------------------------------------------------------

    async def _run_scan(self, incremental: bool) -> None:
        if not self.scanner.is_connected:
            connected = await self.scanner.connect()
            if not connected:
                logger.warning("Scanner unavailable; scan skipped")
                return
        reports = await self.scanner.scan_all_channels(incremental=incremental)
        totals = {
            "scanned": sum(r.get("scanned", 0) for r in reports),
            "matched": sum(r.get("matched", 0) for r in reports),
            "unmatched": sum(r.get("unmatched", 0) for r in reports),
            "skipped": sum(r.get("skipped", 0) for r in reports),
            "updated": sum(r.get("updated", 0) for r in reports),
        }
        logger.info(
            "Scan finished",
            extra={"event": self.INCREMENTAL_SCAN_JOB, "incremental": incremental, **totals},
        )

    async def _job_incremental_scan(self) -> None:
        logger.info("Job start", extra={"job": self.INCREMENTAL_SCAN_JOB})
        try:
            await self._run_scan(incremental=True)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Incremental scan failed", extra={"error": str(exc)})

    async def _job_tmdb_sync(self) -> None:
        """Re-fetch TMDB metadata for all catalog items (slow, 2s between items)."""
        logger.info("Job start", extra={"job": self.TMDB_SYNC_JOB})
        if not self.settings.tmdb_configured:
            logger.warning("TMDB not configured; sync skipped")
            return
        synced = reclassified = failed = 0
        try:
            factory = get_session_factory()
            async with factory() as session:
                ids = (
                    await session.execute(
                        select(CatalogItem.tmdb_id, CatalogItem.content_type)
                    )
                ).all()

            for tmdb_id, content_type in ids:
                try:
                    media_type = "movie" if content_type == "movie" else "tv"
                    data = await self.tmdb.get_normalized_details(tmdb_id, media_type)
                    if not data:
                        failed += 1
                        continue
                    async with factory() as item_session:
                        item = await catalog_service.get_item(item_session, tmdb_id)
                        if item is None:
                            continue
                        changed = await catalog_service.update_item_from_tmdb(
                            item_session, item, data
                        )
                        await item_session.commit()
                        synced += 1
                        reclassified += int(changed)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning(
                        "TMDB sync failed for item",
                        extra={"tmdb_id": tmdb_id, "error": str(exc)},
                    )
                await asyncio.sleep(2)  # run slowly, per spec
        except Exception as exc:  # noqa: BLE001
            logger.exception("TMDB sync job crashed", extra={"error": str(exc)})

        logger.info(
            "Job complete",
            extra={
                "job": self.TMDB_SYNC_JOB,
                "synced": synced,
                "reclassified": reclassified,
                "failed": failed,
            },
        )

    async def _job_health_ping(self) -> None:
        try:
            factory = get_session_factory()
            async with factory() as session:
                counts = await catalog_service.catalog_counts(session)
            logger.info(
                "Scanner health ping",
                extra={
                    "job": self.HEALTH_PING_JOB,
                    "telegram_connected": self.scanner.is_connected,
                    "telegram_configured": self.scanner.is_configured,
                    "catalog_counts": counts,
                    "total_items": sum(counts.values()),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Health ping failed", extra={"error": str(exc)})
