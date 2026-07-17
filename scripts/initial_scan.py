"""One-time FULL historical scan of all configured channels.

Usage (from the project root):

    python scripts/initial_scan.py

Fetches every message from the beginning (incremental=False) for each channel
in TELEGRAM_CHANNELS and runs the clean -> match -> catalog pipeline.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


async def main() -> int:
    from app.config import get_settings
    from app.services.scanner import TelegramScanner
    from app.services.tmdb import TMDBService
    from app.utils.logger import get_logger, setup_logging

    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    logger = get_logger("initial_scan")

    if not settings.telegram_configured:
        logger.error("Telegram is not configured; aborting scan")
        return 1
    if not settings.telegram_channels_list:
        logger.error("TELEGRAM_CHANNELS is empty; aborting scan")
        return 1
    if not settings.tmdb_configured:
        logger.error("TMDB_API_KEY / TMDB_ACCESS_TOKEN missing; aborting scan")
        return 1

    tmdb = TMDBService(settings)
    scanner = TelegramScanner(settings=settings, tmdb_service=tmdb)

    connected = await scanner.connect()
    if not connected:
        logger.error("Could not connect to Telegram; aborting scan")
        await tmdb.aclose()
        return 1

    try:
        logger.info(
            "Starting FULL historical scan",
            extra={"channels": settings.telegram_channels_list},
        )
        reports = await scanner.scan_all_channels(incremental=False)
        for report in reports:
            logger.info("Channel report", extra=report)
        logger.info("Full historical scan complete")
    finally:
        await scanner.disconnect()
        await tmdb.aclose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
