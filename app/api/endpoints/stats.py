"""GROUP 6: Catalog statistics endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.schemas.catalog import RecentItem, StatsResponse
from app.services import catalog_service

router = APIRouter()


@router.get(
    "",
    response_model=StatsResponse,
    summary="Catalog statistics: counts, recent items, top rated, scanner status",
)
async def stats(session: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    data = await catalog_service.stats_data(
        session, channels_configured=len(settings.telegram_channels_list)
    )
    data["recently_added"] = [
        RecentItem.model_validate(item) for item in data["recently_added"]
    ]
    data["top_rated"] = [
        RecentItem.model_validate(item) for item in data["top_rated"]
    ]
    return data
