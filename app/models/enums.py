"""Catalog and content type enumerations."""
from __future__ import annotations

import enum
from typing import Dict


class CatalogType(str, enum.Enum):
    TAMIL_MOVIES = "tamil_movies"
    DUBBED_MOVIES = "dubbed_movies"
    TAMIL_SERIES = "tamil_series"
    OTHER_MOVIES = "other_movies"
    OTHER_SERIES = "other_series"
    ANIME = "anime"


class ContentType(str, enum.Enum):
    MOVIE = "movie"
    SERIES = "series"


CATALOG_METADATA: Dict[CatalogType, Dict[str, str]] = {
    CatalogType.TAMIL_MOVIES: {
        "label": "Tamil Movies",
        "description": "Original Tamil language movies",
    },
    CatalogType.DUBBED_MOVIES: {
        "label": "Dubbed Movies",
        "description": "Movies from any language dubbed into Tamil",
    },
    CatalogType.TAMIL_SERIES: {
        "label": "Tamil Series",
        "description": "Tamil and South Indian series (Tamil originals listed first)",
    },
    CatalogType.OTHER_MOVIES: {
        "label": "Other Movies",
        "description": "Non-South-Indian movies (Hollywood, Korean, Hindi originals, etc.)",
    },
    CatalogType.OTHER_SERIES: {
        "label": "Other Series",
        "description": "Non-South-Indian series (international shows, K-dramas, etc.)",
    },
    CatalogType.ANIME: {
        "label": "Anime",
        "description": "Japanese animated movies and series",
    },
}
