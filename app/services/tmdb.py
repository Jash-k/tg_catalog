"""TMDB API service: async search, confidence scoring, detail normalization."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx
from rapidfuzz import fuzz

from app.config import Settings, get_settings
from app.services.cleaner import CleanedResult
from app.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TMDBCandidate:
    tmdb_id: int
    media_type: str  # 'movie' | 'tv'
    score: int
    title: str
    year: Optional[int]


class TMDBService:
    """Async TMDB client with rate limiting, multi-key rotation, and
    tiered search strategies.

    Multiple API keys: all keys from ``settings.tmdb_api_keys`` are used
    round-robin. A key that TMDB rejects (401/403) is parked for
    ``TMDB_KEY_DISABLE_SECONDS`` and then given another chance; a 429
    rotates to the next key immediately instead of sleeping (sleeping
    remains the fallback when only one usable key is left).
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        headers = {"accept": "application/json"}
        self._uses_token = bool(self.settings.TMDB_ACCESS_TOKEN)
        if self._uses_token:
            headers["Authorization"] = f"Bearer {self.settings.TMDB_ACCESS_TOKEN}"
        self._client = httpx.AsyncClient(
            base_url=self.settings.TMDB_BASE_URL,
            headers=headers,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        self._keys: List[str] = list(self.settings.tmdb_api_keys)
        self._disabled_keys: Dict[str, float] = {}  # key -> parked-at (monotonic)
        self._key_cursor = 0
        self._key_disable_after = float(self.settings.TMDB_KEY_DISABLE_SECONDS)
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._delay = max(float(self.settings.TMDB_RATE_LIMIT_DELAY), 0.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Key pool management
    # ------------------------------------------------------------------

    def _active_keys(self) -> List[str]:
        """Keys not currently parked (parked keys auto-recover after a while)."""
        now = time.monotonic()
        active = []
        for key in self._keys:
            parked_at = self._disabled_keys.get(key)
            if parked_at is None or (now - parked_at) > self._key_disable_after:
                active.append(key)
        return active

    def _current_key(self) -> Optional[str]:
        active = self._active_keys()
        if not active:
            return None
        return active[self._key_cursor % len(active)]

    def _rotate_key(self) -> None:
        active = self._active_keys()
        if active:
            self._key_cursor = (self._key_cursor + 1) % len(active)

    @staticmethod
    def _mask_key(key: Optional[str]) -> str:
        return f"...{key[-4:]}" if key else "none"

    # ------------------------------------------------------------------
    # HTTP plumbing (rate-limited, 429-aware)
    # ------------------------------------------------------------------

    async def _throttle(self) -> None:
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
            self._last_request_at = time.monotonic()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        params = dict(params or {})
        # Room to try every configured key once, plus backoff retries.
        max_attempts = 4 + max(len(self._keys), 1)

        for _attempt in range(max_attempts):
            if not self._uses_token:
                key = self._current_key()
                if key is None:
                    logger.error(
                        "All TMDB API keys are parked/failed; refusing request",
                        extra={"path": path, "keys_configured": len(self._keys)},
                    )
                    return None
                params["api_key"] = key

            await self._throttle()
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                logger.warning("TMDB request error", extra={"path": path, "error": str(exc)})
                return None

            # Invalid/revoked key -> park it and move to the next one.
            if response.status_code in (401, 403) and not self._uses_token:
                bad_key = params.get("api_key")
                if bad_key:
                    self._disabled_keys[bad_key] = time.monotonic()
                    logger.warning(
                        "TMDB rejected API key; parking it and rotating",
                        extra={"key": self._mask_key(bad_key), "status": response.status_code},
                    )
                continue

            # Rate limited -> prefer rotating keys; sleep only as last resort.
            if response.status_code == 429:
                active = self._active_keys()
                if not self._uses_token and len(active) > 1:
                    self._rotate_key()
                    logger.info(
                        "TMDB rate limited; rotating to next API key",
                        extra={"next_key": self._mask_key(self._current_key())},
                    )
                    continue
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after or "").replace(".", "").isdigit() else 5.0
                logger.warning("TMDB rate limited; backing off", extra={"wait_seconds": wait})
                await asyncio.sleep(wait)
                continue

            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                logger.warning(
                    "TMDB HTTP error",
                    extra={"path": path, "status": response.status_code},
                )
                return None
            return response.json()
        return None

    async def ping(self) -> bool:
        if not self.settings.tmdb_configured:
            return False
        data = await self._get("/configuration")
        return data is not None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def _search(
        self,
        media_type: str,
        title: str,
        year: Optional[int],
        language: str,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "query": title,
            "language": language,
            "include_adult": "false",
            "page": 1,
        }
        if year is not None:
            params["year" if media_type == "movie" else "first_air_date_year"] = year
        data = await self._get(f"/search/{media_type}", params)
        if not data:
            return []
        return data.get("results", []) or []

    @staticmethod
    def _candidate_title(result: Dict[str, Any], media_type: str) -> str:
        if media_type == "movie":
            return result.get("title") or result.get("original_title") or ""
        return result.get("name") or result.get("original_name") or ""

    @staticmethod
    def _candidate_year(result: Dict[str, Any], media_type: str) -> Optional[int]:
        raw = (result.get("release_date") or result.get("first_air_date") or "")[:4]
        return int(raw) if raw.isdigit() else None

    def _score_candidate(
        self,
        result: Dict[str, Any],
        *,
        title: str,
        year: Optional[int],
        expected_language: Optional[str],
        media_type: str,
    ) -> int:
        """Confidence score 0-100 per the spec's scoring table."""
        score = 0.0

        # Title fuzzy match: 0-50 points
        candidate_title = self._candidate_title(result, media_type)
        score += (fuzz.ratio(title.lower(), candidate_title.lower()) / 100.0) * 50.0

        # Year match
        candidate_year = self._candidate_year(result, media_type)
        if year is not None and candidate_year is not None:
            if candidate_year == year:
                score += 25
            elif abs(candidate_year - year) == 1:
                score += 15

        # Original language matches expectation (ta for Tamil-tagged files)
        if expected_language and result.get("original_language") == expected_language:
            score += 15

        # Popularity signals
        if (result.get("vote_count") or 0) > 100:
            score += 5
        if (result.get("popularity") or 0) > 10:
            score += 5

        return min(int(round(score)), 100)

    async def find_best_match(self, cleaned: CleanedResult) -> Optional[TMDBCandidate]:
        """Try the spec's search strategies in order; auto-accept >= threshold.

        1. title + year, ta-IN
        2. title + year, en-US
        3. title only, ta-IN
        4. title only, en-US
        5. Progressive: drop the last word of the title and repeat 1-4.
        """
        if not cleaned.cleaned_title:
            return None

        media_type = "movie" if cleaned.content_type == "movie" else "tv"
        expected_language = "ta" if cleaned.is_tamil_audio else None
        threshold = int(self.settings.MIN_CONFIDENCE_SCORE)

        words = cleaned.cleaned_title.split()
        candidate_titles: List[str] = []
        while words:
            candidate_titles.append(" ".join(words))
            if len(words) == 1:
                break
            words = words[:-1]

        best: Optional[TMDBCandidate] = None
        for candidate_title in candidate_titles:
            strategies: List[Tuple[Optional[int], str]] = []
            if cleaned.detected_year:
                strategies.append((cleaned.detected_year, "ta-IN"))
                strategies.append((cleaned.detected_year, "en-US"))
            strategies.append((None, "ta-IN"))
            strategies.append((None, "en-US"))

            for year, language in strategies:
                results = await self._search(media_type, candidate_title, year, language)
                for result in results[:5]:
                    score = self._score_candidate(
                        result,
                        title=candidate_title,
                        year=cleaned.detected_year,
                        expected_language=expected_language,
                        media_type=media_type,
                    )
                    if best is None or score > best.score:
                        best = TMDBCandidate(
                            tmdb_id=result.get("id"),
                            media_type=media_type,
                            score=score,
                            title=self._candidate_title(result, media_type),
                            year=self._candidate_year(result, media_type),
                        )
                    if score >= threshold:
                        return TMDBCandidate(
                            tmdb_id=result.get("id"),
                            media_type=media_type,
                            score=score,
                            title=self._candidate_title(result, media_type),
                            year=self._candidate_year(result, media_type),
                        )
        # Return the best-so-far even if below threshold; the matcher decides.
        return best

    # ------------------------------------------------------------------
    # Details
    # ------------------------------------------------------------------

    def _image_url(self, path: Optional[str], size: str) -> Optional[str]:
        if not path:
            return None
        return f"{self.settings.TMDB_IMAGE_BASE_URL}/{size}{path}"

    @staticmethod
    def _extract_tamil_title(details: Dict[str, Any]) -> Optional[str]:
        block = details.get("translations") or {}
        translations = block.get("translations") or []
        fallback: Optional[str] = None
        for entry in translations:
            if entry.get("iso_639_1") != "ta":
                continue
            data = entry.get("data") or {}
            title = data.get("title") or data.get("name")
            if not title:
                continue
            if entry.get("iso_3166_1") == "IN":
                return title
            fallback = fallback or title
        return fallback

    async def get_raw_details(self, tmdb_id: int, media_type: str) -> Optional[Dict[str, Any]]:
        segment = "movie" if media_type == "movie" else "tv"
        return await self._get(
            f"/{segment}/{tmdb_id}", {"append_to_response": "credits,translations"}
        )

    async def get_normalized_details(
        self, tmdb_id: int, media_type: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch details and normalize them into catalog_items column values."""
        details = await self.get_raw_details(tmdb_id, media_type)
        if not details:
            return None

        is_movie = media_type == "movie"
        title_english = (
            details.get("title") if is_movie else details.get("name")
        ) or details.get("original_title") or details.get("original_name") or ""
        title_original = (
            details.get("original_title") if is_movie else details.get("original_name")
        ) or None

        release_raw = details.get("release_date") if is_movie else details.get("first_air_date")
        release_date = None
        if isinstance(release_raw, str) and len(release_raw) >= 10:
            from datetime import date as _date

            try:
                release_date = _date.fromisoformat(release_raw[:10])
            except ValueError:
                release_date = None
        year = release_date.year if release_date else None

        genres = [g.get("name") for g in details.get("genres", []) if g.get("name")]

        credits = details.get("credits") or {}
        cast_members: List[Dict[str, Any]] = []
        for member in (credits.get("cast") or [])[:10]:
            cast_members.append(
                {
                    "name": member.get("name"),
                    "character": member.get("character"),
                    "profile_url": self._image_url(member.get("profile_path"), "w185"),
                }
            )

        director, director_profile = None, None
        for crew in credits.get("crew") or []:
            if crew.get("job") == "Director":
                director = crew.get("name")
                director_profile = self._image_url(crew.get("profile_path"), "w185")
                break
        if director is None and not is_movie:
            creators = details.get("created_by") or []
            if creators:
                director = ", ".join(c.get("name") for c in creators if c.get("name")) or None
                director_profile = self._image_url(creators[0].get("profile_path"), "w185")

        if is_movie:
            runtime = details.get("runtime")
            total_seasons = None
        else:
            runtimes = details.get("episode_run_time") or []
            runtime = round(sum(runtimes) / len(runtimes)) if runtimes else None
            total_seasons = details.get("number_of_seasons")

        vote_average = details.get("vote_average")
        rating = round(float(vote_average), 1) if vote_average else None

        return {
            "tmdb_id": tmdb_id,
            "content_type": "movie" if is_movie else "series",
            "title_english": title_english.strip(),
            "title_tamil": self._extract_tamil_title(details),
            "title_original": title_original,
            "overview": details.get("overview") or None,
            "tagline": details.get("tagline") or None,
            "year": year,
            "release_date": release_date,
            "poster_url": self._image_url(details.get("poster_path"), "w500"),
            "backdrop_url": self._image_url(details.get("backdrop_path"), "original"),
            "genres": genres,
            "cast_list": cast_members,
            "director": director,
            "director_profile_url": director_profile,
            "rating": rating,
            "vote_count": details.get("vote_count"),
            "runtime": runtime,
            "original_language": details.get("original_language"),
            "total_seasons": total_seasons,
        }
