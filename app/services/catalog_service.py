"""Catalog CRUD operations, discovery queries, search, filters, and stats."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Integer, Text, cast, desc, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import CatalogItem, ScanTracker, UnmatchedItem
from app.models.enums import CATALOG_METADATA, CatalogType
from app.schemas.filters import DiscoverFilters
from app.services.cleaner import CleanedResult
from app.services.matcher import classify_content
from app.utils.helpers import decade_range, language_name, now_utc
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SORT_COLUMNS = {
    "rating": CatalogItem.rating,
    "year": CatalogItem.year,
    "title": func.lower(CatalogItem.title_english),
    "added_at": CatalogItem.added_at,
    "vote_count": CatalogItem.vote_count,
    "runtime": CatalogItem.runtime,
}


# ---------------------------------------------------------------------------
# Ingestion (scanner -> DB)
# ---------------------------------------------------------------------------

async def upsert_item(session: AsyncSession, payload: Dict[str, Any]) -> Tuple[CatalogItem, bool]:
    """Insert a matched item; if the tmdb_id already exists, merge seasons.

    Idempotent: re-running the scanner on the same channel produces the same
    end state (the tmdb_id existence check precedes every insert).
    """
    tmdb_id = payload["tmdb_id"]
    existing = await session.scalar(
        select(CatalogItem).where(CatalogItem.tmdb_id == tmdb_id)
    )
    if existing is not None:
        changed = False
        for season in payload.get("available_seasons") or []:
            seasons = set(existing.available_seasons or [])
            if season not in seasons:
                seasons.add(season)
                existing.available_seasons = sorted(int(s) for s in seasons)
                changed = True
        if changed:
            existing.updated_at = now_utc()
            session.add(existing)
        return existing, False

    item = CatalogItem(**payload)
    session.add(item)
    await session.flush()
    return item, True


def merge_available_seasons(item: CatalogItem, season_number: int) -> bool:
    """Add a season to an existing item; returns True when a change was made."""
    seasons = set(int(s) for s in (item.available_seasons or []))
    if season_number in seasons:
        return False
    seasons.add(int(season_number))
    item.available_seasons = sorted(seasons)
    item.updated_at = now_utc()
    return True


async def find_by_title_year(
    session: AsyncSession, title: str, year: Optional[int]
) -> Optional[CatalogItem]:
    """Dedup helper: does the cleaned title already exist in the catalog?"""
    conditions = [func.lower(CatalogItem.title_english) == title.lower()]
    if year is not None:
        conditions.append(
            or_(CatalogItem.year == year, CatalogItem.year.is_(None))
        )
    return await session.scalar(
        select(CatalogItem).where(*conditions).limit(1)
    )


async def record_unmatched(
    session: AsyncSession,
    *,
    original_filename: str,
    cleaned: Optional[CleanedResult],
    channel_username: str,
    reason: str,
) -> None:
    session.add(
        UnmatchedItem(
            original_filename=original_filename,
            cleaned_title=(cleaned.cleaned_title if cleaned else None),
            detected_year=(cleaned.detected_year if cleaned else None),
            detected_type=(cleaned.content_type if cleaned else None),
            channel_username=channel_username,
            reason=reason,
        )
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Scan tracker
# ---------------------------------------------------------------------------

async def get_or_create_tracker(session: AsyncSession, channel_username: str) -> ScanTracker:
    tracker = await session.scalar(
        select(ScanTracker).where(ScanTracker.channel_username == channel_username)
    )
    if tracker is None:
        tracker = ScanTracker(channel_username=channel_username)
        session.add(tracker)
        await session.flush()
    return tracker


async def scan_tracker_empty(session: AsyncSession) -> bool:
    count = await session.scalar(select(func.count()).select_from(ScanTracker))
    return (count or 0) == 0


# ---------------------------------------------------------------------------
# Discovery / listing
# ---------------------------------------------------------------------------

def _discover_conditions(filters: DiscoverFilters) -> List[Any]:
    conditions: List[Any] = []

    if filters.catalogs:
        conditions.append(
            CatalogItem.catalog_type.in_([c.value for c in filters.catalogs])
        )
    if filters.content_type:
        conditions.append(CatalogItem.content_type == filters.content_type.value)

    if filters.genres:
        if filters.genre_mode == "all":
            # AND logic: jsonb must contain every requested genre
            conditions.append(CatalogItem.genres.contains(filters.genres))
        else:
            # OR logic: any genre key present (jsonb ?| text[])
            conditions.append(
                CatalogItem.genres.op("?|")(cast(filters.genres, ARRAY(Text)))
            )

    if filters.year is not None:
        conditions.append(CatalogItem.year == filters.year)
    if filters.year_from is not None:
        conditions.append(CatalogItem.year >= filters.year_from)
    if filters.year_to is not None:
        conditions.append(CatalogItem.year <= filters.year_to)
    if filters.decade:
        rng = decade_range(filters.decade)
        if rng:
            conditions.append(CatalogItem.year.between(rng[0], rng[1]))

    if filters.rating_min is not None:
        conditions.append(CatalogItem.rating >= filters.rating_min)
    if filters.rating_max is not None:
        conditions.append(CatalogItem.rating <= filters.rating_max)

    if filters.languages:
        conditions.append(CatalogItem.original_language.in_(filters.languages))
    if filters.is_dubbed is not None:
        conditions.append(CatalogItem.is_dubbed.is_(filters.is_dubbed))
    if filters.is_tamil_original is not None:
        conditions.append(CatalogItem.is_tamil_original.is_(filters.is_tamil_original))
    if filters.is_anime is not None:
        conditions.append(CatalogItem.is_anime.is_(filters.is_anime))

    if filters.director:
        conditions.append(CatalogItem.director.ilike(f"%{filters.director.strip()}%"))
    if filters.cast:
        conditions.append(
            cast(CatalogItem.cast_list, Text).ilike(f"%{filters.cast.strip()}%")
        )

    if filters.runtime_min is not None:
        conditions.append(CatalogItem.runtime >= filters.runtime_min)
    if filters.runtime_max is not None:
        conditions.append(CatalogItem.runtime <= filters.runtime_max)

    if filters.has_season:
        conditions.append(CatalogItem.available_seasons.contains(filters.has_season))

    if filters.added_after is not None:
        conditions.append(
            CatalogItem.added_at
            >= datetime.combine(filters.added_after, time.min, tzinfo=timezone.utc)
        )
    if filters.added_before is not None:
        conditions.append(
            CatalogItem.added_at
            <= datetime.combine(filters.added_before, time.max, tzinfo=timezone.utc)
        )

    return conditions


def _order_clause(filters: DiscoverFilters, tamil_first: bool) -> List[Any]:
    column = _SORT_COLUMNS.get(filters.sort, CatalogItem.added_at)
    direction = column.asc() if filters.order == "asc" else column.desc()
    clauses: List[Any] = []
    if tamil_first:
        # Tamil-original series ALWAYS before dubbed series in tamil_series.
        clauses.append(CatalogItem.is_tamil_original.desc())
    clauses.append(direction.nulls_last())
    clauses.append(CatalogItem.tmdb_id.asc())  # stable pagination tiebreak
    return clauses


async def list_items(
    session: AsyncSession,
    filters: DiscoverFilters,
    *,
    tamil_first: bool = False,
) -> Tuple[List[CatalogItem], int]:
    conditions = _discover_conditions(filters)
    total = await session.scalar(
        select(func.count(CatalogItem.id)).where(*conditions)
    ) or 0
    stmt = (
        select(CatalogItem)
        .where(*conditions)
        .order_by(*_order_clause(filters, tamil_first))
        .offset((filters.page - 1) * filters.per_page)
        .limit(filters.per_page)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), total


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

_HEADLINE_OPTS_TITLE = "MaxWords=10, MinWords=1, StartSel=<mark>, StopSel=</mark>, MaxFragments=1"
_HEADLINE_OPTS_OVERVIEW = "MaxWords=30, MinWords=10, StartSel=<mark>, StopSel=</mark>, MaxFragments=2"

# Text-search configuration. Rendered inline ('simple' is a hardcoded constant,
# safe from injection) so asyncpg never has to marshal a regconfig bind param.
_TS_CONFIG = literal("simple")


async def search_items(
    session: AsyncSession,
    *,
    query: str,
    catalog: Optional[str] = None,
    content_type: Optional[str] = None,
    page: int = 1,
    per_page: int = 24,
) -> Tuple[List[Dict[str, Any]], int]:
    """PostgreSQL full-text search with trigram typo-tolerant fallback."""
    base_conditions: List[Any] = []
    if catalog:
        base_conditions.append(CatalogItem.catalog_type == catalog)
    if content_type:
        base_conditions.append(CatalogItem.content_type == content_type)

    # --- primary: tsvector full-text search ------------------------------
    ts_query = func.plainto_tsquery(_TS_CONFIG, query)
    rank = func.ts_rank(CatalogItem.search_vector, ts_query).label("rank")
    fts_conditions = base_conditions + [CatalogItem.search_vector.op("@@")(ts_query)]

    total = await session.scalar(
        select(func.count(CatalogItem.id)).where(*fts_conditions)
    ) or 0

    if total > 0:
        stmt = (
            select(
                CatalogItem,
                rank,
                func.ts_headline(
                    _TS_CONFIG, CatalogItem.title_english, ts_query, _HEADLINE_OPTS_TITLE
                ).label("title_headline"),
                func.ts_headline(
                    _TS_CONFIG,
                    func.coalesce(CatalogItem.overview, ""),
                    ts_query,
                    _HEADLINE_OPTS_OVERVIEW,
                ).label("overview_headline"),
            )
            .where(*fts_conditions)
            .order_by(desc("rank"), CatalogItem.rating.desc().nulls_last())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        rows = (await session.execute(stmt)).all()
        results = [
            {
                "item": row.CatalogItem,
                "score": float(row.rank or 0.0),
                "title_headline": row.title_headline,
                "overview_headline": row.overview_headline,
                "matched_via": "fulltext",
            }
            for row in rows
        ]
        return results, total

    # --- fallback: pg_trgm similarity for typo tolerance -----------------
    similarity = func.similarity(CatalogItem.title_english, query)
    trgm_conditions = base_conditions + [similarity > 0.3]
    total = await session.scalar(
        select(func.count(CatalogItem.id)).where(*trgm_conditions)
    ) or 0
    stmt = (
        select(CatalogItem, similarity.label("score"))
        .where(*trgm_conditions)
        .order_by(desc("score"), CatalogItem.rating.desc().nulls_last())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    rows = (await session.execute(stmt)).all()
    results = [
        {
            "item": row.CatalogItem,
            "score": float(row.score or 0.0),
            "title_headline": None,
            "overview_headline": None,
            "matched_via": "trigram",
        }
        for row in rows
    ]
    return results, total


# ---------------------------------------------------------------------------
# Item detail / similar
# ---------------------------------------------------------------------------

async def get_item(session: AsyncSession, tmdb_id: int) -> Optional[CatalogItem]:
    return await session.scalar(
        select(CatalogItem).where(CatalogItem.tmdb_id == tmdb_id)
    )


async def get_similar(
    session: AsyncSession, item: CatalogItem, limit: int = 5
) -> List[CatalogItem]:
    """Same-catalog items sharing at least one genre, best rated first."""
    conditions = [
        CatalogItem.catalog_type == item.catalog_type,
        CatalogItem.tmdb_id != item.tmdb_id,
    ]
    genres = list(item.genres or [])
    if genres:
        conditions.append(CatalogItem.genres.op("?|")(cast(genres, ARRAY(Text))))
    stmt = (
        select(CatalogItem)
        .where(*conditions)
        .order_by(
            CatalogItem.rating.desc().nulls_last(),
            CatalogItem.vote_count.desc().nulls_last(),
            CatalogItem.added_at.desc(),
        )
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Catalogs summary
# ---------------------------------------------------------------------------

async def catalog_summaries(session: AsyncSession) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for catalog in CatalogType:
        meta = CATALOG_METADATA[catalog]
        cond = CatalogItem.catalog_type == catalog.value
        count = await session.scalar(select(func.count(CatalogItem.id)).where(cond)) or 0
        last_updated = await session.scalar(select(func.max(CatalogItem.updated_at)).where(cond))
        posters = (
            await session.execute(
                select(CatalogItem.poster_url)
                .where(cond, CatalogItem.poster_url.isnot(None))
                .order_by(func.random())
                .limit(3)
            )
        ).scalars().all()
        summaries.append(
            {
                "key": catalog.value,
                "label": meta["label"],
                "description": meta["description"],
                "count": count,
                "last_updated": last_updated,
                "poster_samples": list(posters),
            }
        )
    return summaries


async def catalog_counts(session: AsyncSession) -> Dict[str, int]:
    rows = await session.execute(
        select(CatalogItem.catalog_type, func.count(CatalogItem.id)).group_by(
            CatalogItem.catalog_type
        )
    )
    counts = {c.value: 0 for c in CatalogType}
    counts.update({row[0]: int(row[1]) for row in rows})
    return counts


# ---------------------------------------------------------------------------
# Filter metadata
# ---------------------------------------------------------------------------

async def filters_metadata(
    session: AsyncSession, catalog: Optional[str] = None
) -> Dict[str, Any]:
    scope: List[Any] = [CatalogItem.catalog_type == catalog] if catalog else []

    # Genres (explode jsonb)
    genre_col = func.jsonb_array_elements_text(CatalogItem.genres).table_valued("genre")
    genre_rows = await session.execute(
        select(genre_col.c.genre.label("genre"), func.count().label("count"))
        .where(*scope)
        .group_by(genre_col.c.genre)
        .order_by(desc("count"), genre_col.c.genre)
    )
    genres = [{"name": r.genre, "count": int(r.count)} for r in genre_rows]

    # Years
    year_rows = await session.execute(
        select(CatalogItem.year, func.count().label("count"))
        .where(CatalogItem.year.isnot(None), *scope)
        .group_by(CatalogItem.year)
        .order_by(CatalogItem.year.desc())
    )
    years = [{"year": int(r.year), "count": int(r.count)} for r in year_rows]

    # Decades (floor before casting: CAST rounds in PG, which would put a
    # year like 2027 in the 2030s)
    decade_expr = cast(func.floor(CatalogItem.year / 10), Integer) * 10
    decade_rows = await session.execute(
        select(decade_expr.label("decade"), func.count().label("count"))
        .where(CatalogItem.year.isnot(None), *scope)
        .group_by(decade_expr)
        .order_by(decade_expr.desc())
    )
    decades = [
        {"decade": f"{int(r.decade)}s", "count": int(r.count)}
        for r in decade_rows
        if r.decade is not None
    ]

    # Directors (top 50 by item count)
    director_rows = await session.execute(
        select(CatalogItem.director, func.count().label("count"))
        .where(CatalogItem.director.isnot(None), CatalogItem.director != "", *scope)
        .group_by(CatalogItem.director)
        .order_by(desc("count"), CatalogItem.director)
        .limit(50)
    )
    directors = [{"name": r.director, "count": int(r.count)} for r in director_rows]

    # Languages
    language_rows = await session.execute(
        select(CatalogItem.original_language, func.count().label("count"))
        .where(CatalogItem.original_language.isnot(None), *scope)
        .group_by(CatalogItem.original_language)
        .order_by(desc("count"))
    )
    languages = [
        {
            "code": r.original_language,
            "name": language_name(r.original_language),
            "count": int(r.count),
        }
        for r in language_rows
    ]

    # Ranges
    range_row = (
        await session.execute(
            select(
                func.min(CatalogItem.rating),
                func.max(CatalogItem.rating),
                func.min(CatalogItem.year),
                func.max(CatalogItem.year),
                func.min(CatalogItem.runtime),
                func.max(CatalogItem.runtime),
                func.count(CatalogItem.id),
            ).where(*scope)
        )
    ).one()

    return {
        "genres": genres,
        "years": years,
        "decades": decades,
        "directors": directors,
        "languages": languages,
        "catalog_counts": await catalog_counts(session),
        "rating_range": {
            "min": float(range_row[0]) if range_row[0] is not None else None,
            "max": float(range_row[1]) if range_row[1] is not None else None,
        },
        "year_range": {"min": range_row[2], "max": range_row[3]},
        "runtime_range": {"min": range_row[4], "max": range_row[5]},
        "total_items": int(range_row[6] or 0),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

async def stats_data(session: AsyncSession, channels_configured: int) -> Dict[str, Any]:
    total_items = await session.scalar(select(func.count(CatalogItem.id))) or 0

    by_catalog = await catalog_counts(session)

    type_rows = await session.execute(
        select(CatalogItem.content_type, func.count(CatalogItem.id)).group_by(
            CatalogItem.content_type
        )
    )
    by_type = {"movie": 0, "series": 0}
    by_type.update({row[0]: int(row[1]) for row in type_rows})

    recently_added = list(
        (
            await session.execute(
                select(CatalogItem).order_by(CatalogItem.added_at.desc()).limit(10)
            )
        )
        .scalars()
        .all()
    )

    top_rated = list(
        (
            await session.execute(
                select(CatalogItem)
                .where(CatalogItem.rating.isnot(None))
                .order_by(
                    CatalogItem.rating.desc(),
                    CatalogItem.vote_count.desc().nulls_last(),
                )
                .limit(10)
            )
        )
        .scalars()
        .all()
    )

    genre_col = func.jsonb_array_elements_text(CatalogItem.genres).table_valued("genre")
    genre_rows = await session.execute(
        select(genre_col.c.genre.label("genre"), func.count().label("count"))
        .group_by(genre_col.c.genre)
        .order_by(desc("count"))
        .limit(10)
    )
    top_genres = [{"name": r.genre, "count": int(r.count)} for r in genre_rows]

    tracker_agg = (
        await session.execute(
            select(
                func.count(ScanTracker.id),
                func.coalesce(func.sum(ScanTracker.total_scanned), 0),
                func.coalesce(func.sum(ScanTracker.total_matched), 0),
                func.coalesce(func.sum(ScanTracker.total_unmatched), 0),
                func.max(ScanTracker.last_scanned_at),
            )
        )
    ).one()

    return {
        "total_items": int(total_items),
        "by_catalog": by_catalog,
        "by_type": by_type,
        "recently_added": recently_added,
        "top_rated": top_rated,
        "top_genres": top_genres,
        "scanner_status": {
            "channels_configured": channels_configured,
            "channels_tracked": int(tracker_agg[0] or 0),
            "total_scanned_messages": int(tracker_agg[1] or 0),
            "total_matched": int(tracker_agg[2] or 0),
            "total_unmatched": int(tracker_agg[3] or 0),
            "last_scan_at": tracker_agg[4],
        },
    }


# ---------------------------------------------------------------------------
# TMDB re-sync
# ---------------------------------------------------------------------------

async def update_item_from_tmdb(
    session: AsyncSession, item: CatalogItem, data: Dict[str, Any]
) -> bool:
    """Refresh metadata of an existing item from fresh TMDB data.

    Returns True when the catalog classification changed (reclassification).
    """
    item.title_english = data.get("title_english") or item.title_english
    item.title_tamil = data.get("title_tamil", item.title_tamil)
    item.title_original = data.get("title_original", item.title_original)
    item.overview = data.get("overview", item.overview)
    item.tagline = data.get("tagline", item.tagline)
    item.year = data.get("year", item.year)
    item.release_date = data.get("release_date", item.release_date)
    item.poster_url = data.get("poster_url", item.poster_url)
    item.backdrop_url = data.get("backdrop_url", item.backdrop_url)
    item.genres = data.get("genres", item.genres) or []
    item.cast_list = data.get("cast_list", item.cast_list) or []
    item.director = data.get("director", item.director)
    item.director_profile_url = data.get("director_profile_url", item.director_profile_url)
    item.rating = data.get("rating", item.rating)
    item.vote_count = data.get("vote_count", item.vote_count)
    item.runtime = data.get("runtime", item.runtime)
    item.original_language = data.get("original_language", item.original_language)
    item.total_seasons = data.get("total_seasons", item.total_seasons)
    item.updated_at = now_utc()
    item.tmdb_synced_at = now_utc()

    # Catalog reclassification (e.g. dubbed status discovered later).
    new_catalog = classify_content(
        content_type=item.content_type,
        original_language=item.original_language,
        is_dubbed=item.is_dubbed,
        has_tamil_audio=item.is_dubbed or item.original_language == "ta",
        genres=item.genres or [],
        is_anime_likely=item.is_anime,
    )
    changed = new_catalog.value != item.catalog_type
    if changed:
        logger.info(
            "Reclassifying catalog item",
            extra={
                "tmdb_id": item.tmdb_id,
                "old_catalog": item.catalog_type,
                "new_catalog": new_catalog.value,
            },
        )
        item.catalog_type = new_catalog.value
    item.is_anime = new_catalog == CatalogType.ANIME
    item.is_tamil_original = (item.original_language or "") == "ta"

    session.add(item)
    return changed
