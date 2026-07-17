"""SQLAlchemy ORM models for the catalog, scan tracker, and unmatched items."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, nullable=False)

    catalog_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content_type: Mapped[str] = mapped_column(String(10), nullable=False)

    title_english: Mapped[str] = mapped_column(String(500), nullable=False)
    title_tamil: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    title_original: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    overview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tagline: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    release_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    poster_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    backdrop_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    genres: Mapped[List[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    cast_list: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )

    director: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    director_profile_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    rating: Mapped[Optional[Decimal]] = mapped_column(Numeric(3, 1), nullable=True)
    vote_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    runtime: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    original_language: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

    is_dubbed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    is_tamil_original: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    is_anime: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )

    # Series specific
    available_seasons: Mapped[List[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    total_seasons: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Full-text search (maintained by PostgreSQL trigger, see migration 001)
    search_vector: Mapped[Optional[str]] = mapped_column(TSVECTOR, nullable=True)

    # Tracking
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    tmdb_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_catalog_tmdb_id", "tmdb_id", unique=True),
        Index("idx_catalog_type", "catalog_type"),
        Index("idx_content_type", "content_type"),
        Index("idx_year", "year"),
        Index("idx_rating", "rating"),
        Index("idx_genres", "genres", postgresql_using="gin"),
        Index("idx_cast", "cast_list", postgresql_using="gin"),
        Index("idx_search", "search_vector", postgresql_using="gin"),
        Index("idx_added_at", "added_at"),
        Index("idx_is_dubbed", "is_dubbed"),
        Index("idx_original_language", "original_language"),
        Index("idx_is_tamil_original", "is_tamil_original"),
    )


class ScanTracker(Base):
    """Internal only - NOT exposed via the API."""

    __tablename__ = "scan_tracker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_username: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    last_message_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    total_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    total_matched: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    total_unmatched: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UnmatchedItem(Base):
    """Filenames that could not be matched to TMDB (for review/debugging)."""

    __tablename__ = "unmatched_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    cleaned_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    detected_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    detected_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    channel_username: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
