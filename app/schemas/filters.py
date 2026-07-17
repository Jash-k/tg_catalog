"""Filter/query parameter schemas and FastAPI dependencies."""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.models.enums import CatalogType, ContentType
from app.utils.helpers import decade_range, parse_comma_list

SortField = Literal["rating", "year", "title", "added_at", "vote_count", "runtime"]
CatalogSortField = Literal["added_at", "rating", "year", "title"]
SortOrder = Literal["asc", "desc"]


class DiscoverFilters(BaseModel):
    """Parsed /api/v1/discover query parameters (all optional, combinable)."""

    catalogs: Optional[List[CatalogType]] = None
    content_type: Optional[ContentType] = None
    genres: Optional[List[str]] = None
    genre_mode: Literal["any", "all"] = "any"
    year: Optional[int] = None
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    decade: Optional[str] = None
    rating_min: Optional[float] = None
    rating_max: Optional[float] = None
    languages: Optional[List[str]] = None
    is_dubbed: Optional[bool] = None
    is_tamil_original: Optional[bool] = None
    is_anime: Optional[bool] = None
    director: Optional[str] = None
    cast: Optional[str] = None
    runtime_min: Optional[int] = None
    runtime_max: Optional[int] = None
    has_season: Optional[List[int]] = None
    added_after: Optional[date] = None
    added_before: Optional[date] = None
    sort: SortField = "added_at"
    order: SortOrder = "desc"
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=24, ge=1, le=100)


def _parse_catalogs(raw: Optional[str]) -> Optional[List[CatalogType]]:
    values = parse_comma_list(raw)
    if not values:
        return None
    parsed: List[CatalogType] = []
    valid = {c.value for c in CatalogType}
    for value in values:
        if value not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown catalog '{value}'. Valid: {sorted(valid)}",
            )
        parsed.append(CatalogType(value))
    return parsed


def _parse_content_type(raw: Optional[str]) -> Optional[ContentType]:
    if not raw:
        return None
    try:
        return ContentType(raw.lower())
    except ValueError:
        raise HTTPException(status_code=422, detail="type must be 'movie' or 'series'")


def _parse_seasons(raw: Optional[str]) -> Optional[List[int]]:
    values = parse_comma_list(raw)
    if not values:
        return None
    try:
        return [int(v) for v in values]
    except ValueError:
        raise HTTPException(
            status_code=422, detail="has_season must be comma-separated integers"
        )


def _parse_decade(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    if decade_range(raw) is None:
        raise HTTPException(
            status_code=422,
            detail="decade must look like '1980s', '1990s', '2000s', '2010s', '2020s'",
        )
    return raw.lower()


def _parse_genre_mode(raw: str) -> Literal["any", "all"]:
    value = (raw or "any").lower()
    if value not in ("any", "all"):
        raise HTTPException(status_code=422, detail="genre_mode must be 'any' or 'all'")
    return value  # type: ignore[return-value]


def discover_filters_dep(
    catalog: Optional[str] = Query(None, description="Comma-separated catalog keys"),
    type: Optional[str] = Query(None, description="movie|series"),
    genre: Optional[str] = Query(None, description="Comma-separated genres"),
    genre_mode: str = Query("any", description="any|all"),
    year: Optional[int] = Query(None),
    year_from: Optional[int] = Query(None),
    year_to: Optional[int] = Query(None),
    decade: Optional[str] = Query(None, description="e.g. 2020s"),
    rating_min: Optional[float] = Query(None),
    rating_max: Optional[float] = Query(None),
    original_language: Optional[str] = Query(None, description="Comma-separated TMDB language codes"),
    is_dubbed: Optional[bool] = Query(None),
    is_tamil_original: Optional[bool] = Query(None),
    director: Optional[str] = Query(None),
    cast: Optional[str] = Query(None),
    runtime_min: Optional[int] = Query(None),
    runtime_max: Optional[int] = Query(None),
    has_season: Optional[str] = Query(None, description="Comma-separated season numbers"),
    added_after: Optional[date] = Query(None),
    added_before: Optional[date] = Query(None),
    is_anime: Optional[bool] = Query(None),
    sort: SortField = Query("added_at"),
    order: SortOrder = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(24, ge=1, le=100),
) -> DiscoverFilters:
    return DiscoverFilters(
        catalogs=_parse_catalogs(catalog),
        content_type=_parse_content_type(type),
        genres=parse_comma_list(genre),
        genre_mode=_parse_genre_mode(genre_mode),
        year=year,
        year_from=year_from,
        year_to=year_to,
        decade=_parse_decade(decade),
        rating_min=rating_min,
        rating_max=rating_max,
        languages=[l.lower() for l in parse_comma_list(original_language) or []] or None,
        is_dubbed=is_dubbed,
        is_tamil_original=is_tamil_original,
        is_anime=is_anime,
        director=director,
        cast=cast,
        runtime_min=runtime_min,
        runtime_max=runtime_max,
        has_season=_parse_seasons(has_season),
        added_after=added_after,
        added_before=added_before,
        sort=sort,
        order=order,
        page=page,
        per_page=per_page,
    )


def parse_catalog_key(key: str) -> CatalogType:
    try:
        return CatalogType(key)
    except ValueError:
        valid = sorted(c.value for c in CatalogType)
        raise HTTPException(
            status_code=404, detail=f"Unknown catalog '{key}'. Valid: {valid}"
        )
