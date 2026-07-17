"""GROUP 5: Filter metadata endpoint (all available filter values + counts)."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import CatalogType
from app.schemas.catalog import FiltersResponse
from app.services import catalog_service

router = APIRouter()


@router.get(
    "",
    response_model=FiltersResponse,
    summary="Available filter values with counts (genres, years, directors, ...)",
)
async def filter_metadata(
    catalog: Optional[str] = Query(None, description="Scope metadata to one catalog"),
    session: AsyncSession = Depends(get_db),
) -> dict:
    valid = {c.value for c in CatalogType}
    if catalog is not None and catalog not in valid:
        catalog = None  # unknown catalog -> global metadata
    return await catalog_service.filters_metadata(session, catalog)
