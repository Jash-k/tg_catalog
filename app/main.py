"""FastAPI application entry point.

Startup sequence:
  a. Initialize database connection pool
  b. Run Alembic migrations (also run by startup.sh in containers)
  c. Initialize Telegram client + TMDB service
  d. Start APScheduler (first-ever run triggers a full historical scan)
  e. Register API routes
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.router import api_router
from app.config import get_settings
from app.database import close_db, get_engine, run_migrations
from app.services.scanner import TelegramScanner
from app.services.scheduler import SchedulerService
from app.services.tmdb import TMDBService
from app.utils.logger import get_logger, setup_logging

settings = get_settings()
setup_logging(settings.LOG_LEVEL)
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Optional API-key protection middleware
# ---------------------------------------------------------------------------

class APIKeyMiddleware(BaseHTTPMiddleware):
    """If API_SECRET_KEY is set, every /api/ request (except /api/v1/health)
    must carry a matching ``X-API-Key`` header."""

    async def dispatch(self, request: Request, call_next):
        secret = settings.api_secret_key_or_none
        if (
            secret
            and request.url.path.startswith("/api/")
            and not request.url.path.endswith("/health")
        ):
            provided = request.headers.get("X-API-Key") or request.query_params.get("api_key")
            if provided != secret:
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": {"code": 401, "message": "Invalid or missing X-API-Key header"},
                    },
                )
        return await call_next(request)


# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Tamil Content Catalog", extra={"version": __version__})

    # a. Database pool
    get_engine()
    app.state.settings = settings

    # b. Migrations (startup.sh also runs these; upgrade head is idempotent)
    try:
        await run_migrations()
    except Exception as exc:  # noqa: BLE001 - never block the API from booting
        logger.error("In-app migration run failed", extra={"error": str(exc)})

    # c. External services
    tmdb = TMDBService(settings)
    scanner = TelegramScanner(settings=settings, tmdb_service=tmdb)
    app.state.tmdb = tmdb
    app.state.scanner = scanner

    if scanner.is_configured:
        asyncio.create_task(scanner.connect())  # non-blocking startup
    else:
        logger.warning("Telegram credentials missing; scanner disabled")

    # d. Scheduler (handles first-run full scan + periodic jobs)
    scheduler = SchedulerService(scanner=scanner, tmdb_service=tmdb, settings=settings)
    scheduler.start()
    app.state.scheduler = scheduler

    logger.info("Startup complete")
    yield

    # Shutdown
    logger.info("Shutting down")
    scheduler.shutdown()
    await scanner.disconnect()
    await tmdb.aclose()
    await close_db()


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Tamil Content Catalog API",
    description=(
        "Private metadata catalog: Telegram channels -> cleaned titles -> "
        "TMDB metadata -> 6 organized catalogs with a rich filter/search API. "
        "Metadata only - no file links, no message IDs."
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# CORS: permissive in development, restrict via ALLOWED_ORIGINS in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(APIKeyMiddleware)


# ---------------------------------------------------------------------------
# Consistent error envelope
# ---------------------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {"code": exc.status_code, "message": exc.detail},
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {"code": 422, "message": "Validation error", "details": exc.errors()},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API error", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {"code": 500, "message": "Internal server error"},
        },
    )


# e. API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "Tamil Content Catalog API",
        "version": __version__,
        "docs": "/docs",
        "redoc": "/redoc",
        "api": "/api/v1",
        "health": "/api/v1/health",
    }
