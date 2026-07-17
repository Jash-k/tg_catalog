"""GROUP 1: Catalog browse endpoints."""
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enums import CatalogType
from app.schemas.catalog import (
    ApiResponse,
    CatalogItemOut,
    CatalogsResponse,
    Meta,
)
from app.schemas.filters import DiscoverFilters, parse_catalog_key
from app.services import catalog_service
from app.utils.helpers import build_meta

router = APIRouter()


@router.get(
    "",
    response_model=CatalogsResponse,
    summary="List all 6 catalogs with counts and poster samples",
)
async def list_catalogs(session: AsyncSession = Depends(get_db)) -> dict:
    summaries = await catalog_service.catalog_summaries(session)
    return {"catalogs": summaries}


@router.get(
    "/{catalog_key}",
    response_model=ApiResponse[List[CatalogItemOut]],
    summary="Paginated browse of one catalog",
)
async def browse_catalog(
    catalog_key: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=50),
    sort: str = Query("added_at", pattern="^(added_at|rating|year|title)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[List[CatalogItemOut]]:
    catalog: CatalogType = parse_catalog_key(catalog_key)
    filters = DiscoverFilters(
        catalogs=[catalog],
        sort=sort,  # type: ignore[arg-type]
        order=order,  # type: ignore[arg-type]
        page=page,
        per_page=per_page,
    )
    # Special rule: Tamil-original series always first in tamil_series.
    tamil_first = catalog == CatalogType.TAMIL_SERIES
    items, total = await catalog_service.list_items(
        session, filters, tamil_first=tamil_first
    )
    return ApiResponse(
        data=[CatalogItemOut.model_validate(item) for item in items],
        meta=Meta(
            **build_meta(total=total, page=page, per_page=per_page, catalog=catalog.value)
        ),
    )
