"""GROUP 3: Full-text search endpoint (tsvector + trigram fallback)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.catalog import ApiResponse, Meta, SearchHit
from app.services import catalog_service
from app.utils.helpers import build_meta

router = APIRouter()


@router.get(
    "",
    response_model=ApiResponse[List[SearchHit]],
    summary="Full-text search across titles, director, cast, overview",
)
async def search(
    q: str = Query(..., min_length=2, description="Search query (min 2 chars)"),
    catalog: Optional[str] = Query(None, description="Limit to one catalog"),
    type: Optional[str] = Query(None, pattern="^(movie|series)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[List[SearchHit]]:
    query = q.strip()
    if len(query) < 2:
        query = query.ljust(2)  # unreachable due to min_length, kept for safety

    results, total = await catalog_service.search_items(
        session,
        query=query,
        catalog=catalog,
        content_type=type,
        page=page,
        per_page=per_page,
    )
    hits: List[SearchHit] = []
    for result in results:
        item_kwargs = SearchHit.model_validate(result["item"]).model_dump()
        item_kwargs.update(
            {
                "title_headline": result["title_headline"],
                "overview_headline": result["overview_headline"],
                "score": result["score"],
                "matched_via": result["matched_via"],
            }
        )
        hits.append(SearchHit(**item_kwargs))

    return ApiResponse(
        data=hits,
        meta=Meta(
            **build_meta(total=total, page=page, per_page=per_page, catalog=catalog)
        ),
    )
