"""Lenient Pydantic schemas for raw TMDB API responses."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class TMDBSearchResult(BaseModel):
    """One result from /search/movie or /search/tv (extra fields ignored)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    title: Optional[str] = None          # movies
    name: Optional[str] = None           # tv
    original_title: Optional[str] = None
    original_name: Optional[str] = None
    release_date: Optional[str] = None
    first_air_date: Optional[str] = None
    original_language: Optional[str] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    popularity: Optional[float] = None
    poster_path: Optional[str] = None
    overview: Optional[str] = None

    @property
    def display_title(self) -> str:
        return self.title or self.name or self.original_title or self.original_name or ""

    @property
    def year(self) -> Optional[int]:
        raw = (self.release_date or self.first_air_date or "")[:4]
        return int(raw) if raw.isdigit() else None


class TMDBSearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int = 1
    results: List[TMDBSearchResult] = []
    total_results: int = 0
