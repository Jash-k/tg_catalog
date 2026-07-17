"""Main API router - mounts every endpoint group under /api/v1."""
from fastapi import APIRouter

from app.api.endpoints import catalog, discover, filters, health, items, search, stats

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(catalog.router, prefix="/catalogs", tags=["catalogs"])
api_router.include_router(discover.router, prefix="/discover", tags=["discover"])
api_router.include_router(search.router, prefix="/search", tags=["search"])
api_router.include_router(items.router, prefix="/items", tags=["items"])
api_router.include_router(filters.router, prefix="/filters", tags=["filters"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
