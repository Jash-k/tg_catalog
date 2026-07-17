"""Re-sync TMDB metadata for every item in the catalog.

Refreshes ratings, translations (incl. Tamil titles), cast, posters, and
re-runs catalog classification. Runs slowly (2 seconds between items).

Usage (from the project root):

    python scripts/sync_tmdb.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


async def main() -> int:
    from sqlalchemy import select

    from app.config import get_settings
    from app.database import get_session_factory
    from app.models.catalog import CatalogItem
    from app.services import catalog_service
    from app.services.tmdb import TMDBService
    from app.utils.logger import get_logger, setup_logging

    settings = get_settings()
    setup_logging(settings.LOG_LEVEL)
    logger = get_logger("sync_tmdb")

    if not settings.tmdb_configured:
        logger.error("TMDB_API_KEY / TMDB_ACCESS_TOKEN missing; aborting sync")
        return 1

    tmdb = TMDBService(settings)
    factory = get_session_factory()

    async with factory() as session:
        rows = (
            await session.execute(
                select(CatalogItem.tmdb_id, CatalogItem.content_type, CatalogItem.title_english)
            )
        ).all()

    logger.info("TMDB sync starting", extra={"items": len(rows)})
    synced = reclassified = failed = 0

    for tmdb_id, content_type, title in rows:
        try:
            media_type = "movie" if content_type == "movie" else "tv"
            data = await tmdb.get_normalized_details(tmdb_id, media_type)
            if not data:
                failed += 1
                logger.warning("No TMDB details", extra={"tmdb_id": tmdb_id, "title": title})
                continue
            async with factory() as item_session:
                item = await catalog_service.get_item(item_session, tmdb_id)
                if item is None:
                    continue
                changed = await catalog_service.update_item_from_tmdb(item_session, item, data)
                await item_session.commit()
                synced += 1
                reclassified += int(changed)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning(
                "Sync failed for item",
                extra={"tmdb_id": tmdb_id, "title": title, "error": str(exc)},
            )
        await asyncio.sleep(2)

    await tmdb.aclose()
    logger.info(
        "TMDB sync complete",
        extra={"synced": synced, "reclassified": reclassified, "failed": failed},
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
