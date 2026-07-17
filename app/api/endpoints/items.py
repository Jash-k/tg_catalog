"""GROUP 4: Single item detail endpoint with similar-item recommendations."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas.catalog import ApiResponse, ItemDetail, SimilarItemOut
from app.services import catalog_service

router = APIRouter()


@router.get(
    "/{tmdb_id}",
    response_model=ApiResponse[ItemDetail],
    summary="Full metadata for one item + 5 similar items",
)
async def item_detail(
    tmdb_id: int,
    session: AsyncSession = Depends(get_db),
) -> ApiResponse[ItemDetail]:
    item = await catalog_service.get_item(session, tmdb_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No catalog item with tmdb_id={tmdb_id}")

    similar = await catalog_service.get_similar(session, item, limit=5)
    detail = ItemDetail.model_validate(item)
    detail.similar = [SimilarItemOut.model_validate(entry) for entry in similar]
    return ApiResponse(data=detail)
