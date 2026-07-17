"""Stremio addon protocol endpoints (catalog-only addon).

Stremio calls these standard paths at the domain root:

    GET /manifest.json
    GET /catalog/{type}/{catalog_id}.json
    GET /catalog/{type}/{catalog_id}/{extra}.json   (extras: skip, search, genre)
    GET /meta/{type}/{id}.json

Design notes:
  - This addon advertises "catalog" and "meta" resources ONLY. It never
    serves streams - playback sources come from whatever streaming addons
    the user has installed, which matches this project's metadata-only rule
    (no file links, no message IDs anywhere in the system).
  - Item IDs use the "tmdb:{tmdb_id}" scheme and are shared between the
    catalog and meta endpoints.
  - CORS must effectively allow "*" for Stremio clients (the API's CORS
    middleware already covers every route; keep ALLOWED_ORIGINS permissive
    if you intend to use this addon from Stremio).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.database import get_db
from app.models.catalog import CatalogItem
from app.models.enums import CATALOG_METADATA, CatalogType, ContentType
from app.schemas.filters import DiscoverFilters
from app.services import catalog_service

router = APIRouter()

# Stremio pages catalogs with a skip extra; 100 is the common page size
# (the discover filter allows up to 100).
STREMIO_PAGE_SIZE = 100

# Anime is one catalog internally but Stremio requires one type per
# catalog, so it is exposed as two: anime_movies + anime_series.
_STREMIO_CATALOG_MAP: Dict[str, Tuple[CatalogType, ContentType]] = {
    "tamil_movies": (CatalogType.TAMIL_MOVIES, ContentType.MOVIE),
    "dubbed_movies": (CatalogType.DUBBED_MOVIES, ContentType.MOVIE),
    "other_movies": (CatalogType.OTHER_MOVIES, ContentType.MOVIE),
    "anime_movies": (CatalogType.ANIME, ContentType.MOVIE),
    "tamil_series": (CatalogType.TAMIL_SERIES, ContentType.SERIES),
    "other_series": (CatalogType.OTHER_SERIES, ContentType.SERIES),
    "anime_series": (CatalogType.ANIME, ContentType.SERIES),
}

_STREMIO_TITLES: Dict[str, str] = {
    "tamil_movies": CATALOG_METADATA[CatalogType.TAMIL_MOVIES]["label"],
    "dubbed_movies": CATALOG_METADATA[CatalogType.DUBBED_MOVIES]["label"],
    "other_movies": CATALOG_METADATA[CatalogType.OTHER_MOVIES]["label"],
    "anime_movies": f'{CATALOG_METADATA[CatalogType.ANIME]["label"]} - Movies',
    "tamil_series": CATALOG_METADATA[CatalogType.TAMIL_SERIES]["label"],
    "other_series": CATALOG_METADATA[CatalogType.OTHER_SERIES]["label"],
    "anime_series": f'{CATALOG_METADATA[CatalogType.ANIME]["label"]} - Series',
}

_ITEM_ID_RE = re.compile(r"^tmdb:(\d+)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stremio_id(item: CatalogItem) -> str:
    return f"tmdb:{item.tmdb_id}"


def _meta_from_item(item: CatalogItem, *, full: bool = False) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "id": _stremio_id(item),
        "type": item.content_type,
        "name": item.title_english,
        "poster": item.poster_url,
        "posterShape": "poster",
    }
    # Optional fields - include whenever present (harmless in summaries too).
    if item.backdrop_url:
        meta["background"] = item.backdrop_url
    if item.overview:
        meta["description"] = item.overview
    if item.year:
        meta["releaseInfo"] = str(item.year)
    if item.release_date:
        meta["released"] = f"{item.release_date.isoformat()}T00:00:00.000Z"
    if item.rating is not None:
        meta["imdbRating"] = f"{float(item.rating):.1f}"
    if item.genres:
        meta["genres"] = list(item.genres)
    cast = [m.get("name") for m in (item.cast_list or []) if m.get("name")]
    if cast:
        meta["cast"] = cast
    if item.director:
        meta["director"] = [d.strip() for d in item.director.split(",") if d.strip()]
    if item.runtime and item.content_type == "movie":
        meta["runtime"] = f"{item.runtime} min"
    if full and item.title_tamil:
        meta["originalName"] = item.title_tamil
    return meta


def _parse_extra(extra: Optional[str]) -> Dict[str, str]:
    """Parse Stremio's extra segment: 'skip=100&search=vikram&genre=Action'."""
    parsed: Dict[str, str] = {}
    if not extra:
        return parsed
    for chunk in extra.split("&"):
        if "=" in chunk:
            key, value = chunk.split("=", 1)
            parsed[unquote(key).strip()] = unquote(value).strip()
    return parsed


def _not_found(message: str) -> JSONResponse:
    return JSONResponse({"err": message}, status_code=404)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@router.get("/manifest.json", include_in_schema=False)
async def stremio_manifest() -> Dict[str, Any]:
    catalogs = [
        {
            "type": content_type,
            "id": catalog_id,
            "name": _STREMIO_TITLES[catalog_id],
            "extraSupported": ["skip", "search", "genre"],
            "extraRequired": [],
        }
        for catalog_id, (_, content_type) in _STREMIO_CATALOG_MAP.items()
    ]
    return {
        "id": "community.tamilcatalog",
        "version": __version__,
        "name": "Tamil Catalog",
        "description": (
            "Tamil movies, dubbed movies, Tamil & South Indian series, "
            "international catalog and anime - a private TMDB metadata catalog. "
            "Catalogs + metadata only; no streams."
        ),
        "resources": ["catalog", "meta"],
        "types": ["movie", "series"],
        "idPrefixes": ["tmdb:"],
        "catalogs": catalogs,
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@router.get("/catalog/{content_type}/{catalog_id}.json", include_in_schema=False)
@router.get("/catalog/{content_type}/{catalog_id}/{extra}.json", include_in_schema=False)
async def stremio_catalog(
    content_type: str,
    catalog_id: str,
    extra: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
) -> Any:
    definition = _STREMIO_CATALOG_MAP.get(catalog_id)
    if definition is None:
        return _not_found(f"Unknown catalog '{catalog_id}'")
    db_catalog, db_content_type = definition

    # The catalog id already encodes movie vs series; a mismatched type in
    # the URL path is treated as an empty page (Stremio probes both types).
    if content_type != db_content_type.value:
        return {"metas": []}

    extras = _parse_extra(extra)
    search_query = extras.get("search")
    genre = extras.get("genre")
    try:
        skip = max(int(extras.get("skip", "0")), 0)
    except ValueError:
        skip = 0

    if search_query and len(search_query.strip()) >= 2:
        results, _total = await catalog_service.search_items(
            session,
            query=search_query.strip(),
            catalog=db_catalog.value,
            content_type=db_content_type.value,
            page=1,
            per_page=STREMIO_PAGE_SIZE,
        )
        items = [result["item"] for result in results]
    else:
        filters = DiscoverFilters(
            catalogs=[db_catalog],
            content_type=db_content_type,
            genres=[genre] if genre else None,
            genre_mode="any",
            sort="added_at",
            order="desc",
            page=(skip // STREMIO_PAGE_SIZE) + 1,
            per_page=STREMIO_PAGE_SIZE,
        )
        items, _total = await catalog_service.list_items(
            session,
            filters,
            tamil_first=(db_catalog == CatalogType.TAMIL_SERIES),
        )

    return {"metas": [_meta_from_item(item) for item in items]}


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@router.get("/meta/{content_type}/{item_id}.json", include_in_schema=False)
async def stremio_meta(
    content_type: str,
    item_id: str,
    session: AsyncSession = Depends(get_db),
) -> Any:
    match = _ITEM_ID_RE.match(item_id)
    if not match:
        return _not_found(f"Unsupported id '{item_id}' (expected 'tmdb:<id>')")

    item = await catalog_service.get_item(session, int(match.group(1)))
    if item is None:
        return _not_found(f"No catalog item with id '{item_id}'")

    return {"meta": _meta_from_item(item, full=True)}
