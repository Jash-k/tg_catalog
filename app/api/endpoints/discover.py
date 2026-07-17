"""GROUP 2: Discover endpoint with rich combinable filtering."""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.catalog import ApiResponse, CatalogItemOut, Meta
from app.schemas.filters import DiscoverFilters, discover_filters_dep
from app.services import catalog_service
from app.utils.helpers import build_meta

router = APIRouter()


@router.get(
    "",
    response_model=ApiResponse[List[CatalogItemOut]],
    summary="Discover content with genre/year/rating/language/person filters",
)
async def discover(
    filters: DiscoverFilters = Depends(discover_filters_dep),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[List[CatalogItemOut]]:
    # tamil_first only applies when tamil_series is the sole selected catalog.
    tamil_first = bool(
        filters.catalogs
        and len(filters.catalogs) == 1
        and filters.catalogs[0].value == "tamil_series"
    )
    items, total = await catalog_service.list_items(
        session, filters, tamil_first=tamil_first
    )
    catalog_label = None
    if filters.catalogs:
        catalog_label = ",".join(c.value for c in filters.catalogs)
    return ApiResponse(
        data=[CatalogItemOut.model_validate(item) for item in items],
        meta=Meta(
            **build_meta(
                total=total,
                page=filters.page,
                per_page=filters.per_page,
                catalog=catalog_label,
            )
        ),
    )
