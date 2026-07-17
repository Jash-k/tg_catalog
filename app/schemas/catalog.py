"""Pydantic response schemas for catalog items, filters, stats, and health."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Item schemas
# ---------------------------------------------------------------------------

class CastMember(BaseModel):
    name: str
    character: Optional[str] = None
    profile_url: Optional[str] = None


class CatalogItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tmdb_id: int
    catalog_type: str
    content_type: str
    title_english: str
    title_tamil: Optional[str] = None
    title_original: Optional[str] = None
    overview: Optional[str] = None
    tagline: Optional[str] = None
    year: Optional[int] = None
    release_date: Optional[date] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    cast_list: List[CastMember] = Field(default_factory=list)
    director: Optional[str] = None
    director_profile_url: Optional[str] = None
    rating: Optional[float] = None
    vote_count: Optional[int] = None
    runtime: Optional[int] = None
    original_language: Optional[str] = None
    is_dubbed: bool = False
    is_tamil_original: bool = False
    is_anime: bool = False
    available_seasons: Optional[List[int]] = None
    total_seasons: Optional[int] = None
    added_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("rating", mode="before")
    @classmethod
    def _decimal_to_float(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        return value


class SimilarItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tmdb_id: int
    title_english: str
    title_tamil: Optional[str] = None
    year: Optional[int] = None
    poster_url: Optional[str] = None
    rating: Optional[float] = None
    catalog_type: str
    content_type: str

    @field_validator("rating", mode="before")
    @classmethod
    def _decimal_to_float(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        return value


class ItemDetail(CatalogItemOut):
    similar: List[SimilarItemOut] = Field(default_factory=list)


class SearchHit(CatalogItemOut):
    title_headline: Optional[str] = None
    overview_headline: Optional[str] = None
    score: float = 0.0
    matched_via: str = "fulltext"  # 'fulltext' | 'trigram'


# ---------------------------------------------------------------------------
# Generic response envelope
# ---------------------------------------------------------------------------

class Meta(BaseModel):
    total: int
    page: int
    per_page: int
    total_pages: int
    catalog: Optional[str] = None


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    meta: Optional[Meta] = None


# ---------------------------------------------------------------------------
# Catalogs summary
# ---------------------------------------------------------------------------

class CatalogSummary(BaseModel):
    key: str
    label: str
    description: str
    count: int
    last_updated: Optional[datetime] = None
    poster_samples: List[str] = Field(default_factory=list)


class CatalogsResponse(BaseModel):
    catalogs: List[CatalogSummary]


# ---------------------------------------------------------------------------
# Filters metadata
# ---------------------------------------------------------------------------

class GenreCount(BaseModel):
    name: str
    count: int


class YearCount(BaseModel):
    year: int
    count: int


class DecadeCount(BaseModel):
    decade: str
    count: int


class DirectorCount(BaseModel):
    name: str
    count: int


class LanguageCount(BaseModel):
    code: str
    name: str
    count: int


class RangeValue(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class FiltersResponse(BaseModel):
    genres: List[GenreCount]
    years: List[YearCount]
    decades: List[DecadeCount]
    directors: List[DirectorCount]
    languages: List[LanguageCount]
    catalog_counts: Dict[str, int]
    rating_range: RangeValue
    year_range: RangeValue
    runtime_range: RangeValue
    total_items: int


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class RecentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tmdb_id: int
    title_english: str
    catalog_type: str
    content_type: str
    poster_url: Optional[str] = None
    year: Optional[int] = None
    rating: Optional[float] = None
    added_at: Optional[datetime] = None

    @field_validator("rating", mode="before")
    @classmethod
    def _decimal_to_float(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        return value


class ScannerStatus(BaseModel):
    channels_configured: int
    channels_tracked: int = 0
    last_scan_at: Optional[datetime] = None
    total_scanned_messages: int = 0
    total_matched: int = 0
    total_unmatched: int = 0


class StatsResponse(BaseModel):
    total_items: int
    by_catalog: Dict[str, int]
    by_type: Dict[str, int]
    recently_added: List[RecentItem]
    top_rated: List[RecentItem]
    top_genres: List[GenreCount]
    scanner_status: ScannerStatus


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    database: str
    telegram: str
    tmdb: str
    version: str
    uptime_seconds: int
