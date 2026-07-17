"""GROUP 7: Health check endpoint (used by Railway healthchecks)."""
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.database import get_db

router = APIRouter()

_STARTED_AT = time.monotonic()


@router.get(
    "/health",
    summary="Service health: database, telegram, tmdb connectivity",
)
async def health(request: Request, session: AsyncSession = Depends(get_db)) -> JSONResponse:
    # Database
    try:
        await session.execute(text("SELECT 1"))
        database_status = "connected"
    except Exception:  # noqa: BLE001
        database_status = "disconnected"

    # Telegram
    scanner = getattr(request.app.state, "scanner", None)
    if scanner is None or not scanner.is_configured:
        telegram_status = "not_configured"
    else:
        telegram_status = "connected" if scanner.is_connected else "disconnected"

    # TMDB
    tmdb = getattr(request.app.state, "tmdb", None)
    if tmdb is None or not tmdb.settings.tmdb_configured:
        tmdb_status = "not_configured"
    else:
        try:
            tmdb_status = "connected" if await tmdb.ping() else "disconnected"
        except Exception:  # noqa: BLE001
            tmdb_status = "disconnected"

    healthy = database_status == "connected"
    payload = {
        "status": "healthy" if healthy else "degraded",
        "database": database_status,
        "telegram": telegram_status,
        "tmdb": tmdb_status,
        "version": __version__,
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
    }
    return JSONResponse(payload, status_code=200 if healthy else 503)
