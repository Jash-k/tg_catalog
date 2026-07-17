"""Title matching: combine cleaner output + TMDB results, classify catalogs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.models.enums import CatalogType
from app.services.cleaner import CleanedResult
from app.services.tmdb import TMDBService
from app.utils.helpers import ANIMATION_GENRE, SOUTH_INDIAN_LANGUAGES
from app.utils.logger import get_logger

logger = get_logger(__name__)


def classify_content(
    *,
    content_type: str,
    original_language: Optional[str],
    is_dubbed: bool,
    has_tamil_audio: bool,
    genres: Optional[List[str]] = None,
    is_anime_likely: bool = False,
) -> CatalogType:
    """Classify content into EXACTLY ONE catalog.

    Priority order (per spec):
      1. Anime        - Japanese + (Animation genre OR anime keywords)
      2. Tamil Movies - ta original, not dubbed, movie
      3. Dubbed Movies- any language dubbed to Tamil, movie
      4. Tamil Series - South Indian series (ta/te/ml/kn)
      5. Other Movies - non-South-Indian movies (catch-all for movies)
      6. Other Series - non-South-Indian series (catch-all for series)
    """
    language = (original_language or "").lower()
    genres = genres or []
    is_south_indian = language in SOUTH_INDIAN_LANGUAGES

    # 1. Anime takes priority over Other Movies / Other Series.
    if language == "ja" and (ANIMATION_GENRE in genres or is_anime_likely):
        return CatalogType.ANIME

    # 2. Original Tamil movies.
    if content_type == "movie" and language == "ta" and not is_dubbed:
        return CatalogType.TAMIL_MOVIES

    # 3. Movies found with Tamil audio but originally another language.
    if content_type == "movie" and is_dubbed:
        return CatalogType.DUBBED_MOVIES

    # 4. South Indian series (includes Tamil originals and te/ml/kn series
    #    found in Tamil channels). Tamil originals sort first downstream via
    #    the is_tamil_original flag.
    if content_type == "series" and is_south_indian and (has_tamil_audio or language == "ta"):
        return CatalogType.TAMIL_SERIES

    # 5. Catch-all for remaining movies (non-South-Indian originals).
    if content_type == "movie":
        return CatalogType.OTHER_MOVIES

    # 6. Catch-all for remaining series (non-South-Indian originals,
    #    or South-Indian series with no Tamil audio signal).
    return CatalogType.OTHER_SERIES


@dataclass
class MatchOutcome:
    matched: bool
    reason: Optional[str] = None  # 'no_tmdb_match' | 'low_confidence' | 'parse_failed'
    score: int = 0
    data: Optional[Dict[str, Any]] = None  # catalog_items payload


class TitleMatcher:
    """Bridge between the cleaning pipeline, TMDB, and catalog classification."""

    def __init__(
        self,
        tmdb_service: TMDBService,
        settings: Optional[Settings] = None,
    ) -> None:
        self.tmdb = tmdb_service
        self.settings = settings or get_settings()

    async def match(self, cleaned: CleanedResult) -> MatchOutcome:
        if cleaned.parse_failed or not cleaned.cleaned_title:
            return MatchOutcome(matched=False, reason="parse_failed")

        candidate = await self.tmdb.find_best_match(cleaned)
        if candidate is None or not candidate.tmdb_id:
            return MatchOutcome(matched=False, reason="no_tmdb_match")

        if candidate.score < int(self.settings.MIN_CONFIDENCE_SCORE):
            logger.info(
                "TMDB match below confidence threshold",
                extra={
                    "title": cleaned.cleaned_title,
                    "tmdb_id": candidate.tmdb_id,
                    "score": candidate.score,
                },
            )
            return MatchOutcome(matched=False, reason="low_confidence", score=candidate.score)

        details = await self.tmdb.get_normalized_details(candidate.tmdb_id, candidate.media_type)
        if not details:
            return MatchOutcome(matched=False, reason="no_tmdb_match", score=candidate.score)

        original_language = (details.get("original_language") or "").lower() or None
        is_dubbed = bool(cleaned.is_tamil_audio and original_language and original_language != "ta")
        catalog_type = classify_content(
            content_type=details["content_type"],
            original_language=original_language,
            is_dubbed=is_dubbed,
            has_tamil_audio=cleaned.is_tamil_audio,
            genres=details.get("genres") or [],
            is_anime_likely=cleaned.is_anime_likely,
        )

        available_seasons: List[int] = []
        if details["content_type"] == "series" and cleaned.season_number is not None:
            available_seasons = [cleaned.season_number]

        payload: Dict[str, Any] = {
            **details,
            "catalog_type": catalog_type.value,
            "is_dubbed": is_dubbed,
            "is_tamil_original": original_language == "ta",
            "is_anime": catalog_type == CatalogType.ANIME,
            "available_seasons": available_seasons,
        }
        return MatchOutcome(matched=True, score=candidate.score, data=payload)
