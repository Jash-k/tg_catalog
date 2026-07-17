"""Shared utilities and constants (stdlib-only so it imports anywhere)."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

SOUTH_INDIAN_LANGUAGES = {"ta", "te", "ml", "kn"}

LANGUAGE_NAMES: Dict[str, str] = {
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "kn": "Kannada",
    "hi": "Hindi",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "cn": "Chinese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "th": "Thai",
    "tr": "Turkish",
    "id": "Indonesian",
    "mr": "Marathi",
    "bn": "Bengali",
    "pa": "Punjabi",
    "ur": "Urdu",
    "fa": "Persian",
}

ANIMATION_GENRE = "Animation"

ANIME_KEYWORDS = {"anime", "crunchyroll", "funimation", "subindo", "varyg"}

ANIME_RELEASE_GROUPS = {"subsplease", "erai-raws", "horriblesubs", "yuisubs", "judas"}

VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-matroska",
    "video/avi",
    "video/x-msvideo",
    "video/quicktime",
    "application/octet-stream",
    "video/webm",
    "video/mpeg",
}

VIDEO_EXTENSIONS = (
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".3gp",
)

ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".gz")

_DECADE_RE = re.compile(r"^\s*((?:19|20)\d0)s\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def language_name(code: Optional[str]) -> str:
    if not code:
        return "Unknown"
    return LANGUAGE_NAMES.get(code.lower(), code.upper())


def decade_range(decade: Optional[str]) -> Optional[Tuple[int, int]]:
    """Convert '2020s' -> (2020, 2029). Returns None for invalid input."""
    if not decade:
        return None
    match = _DECADE_RE.match(decade)
    if not match:
        return None
    start = int(match.group(1))
    return start, start + 9


def build_meta(
    total: int,
    page: int,
    per_page: int,
    catalog: Optional[str] = None,
) -> Dict[str, Any]:
    total_pages = math.ceil(total / per_page) if per_page else 0
    meta: Dict[str, Any] = {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }
    if catalog is not None:
        meta["catalog"] = catalog
    return meta


def parse_comma_list(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


_DB_CREDENTIALS_RE = re.compile(r"(://[^:/\s]+:)[^@\s]+(@)")


def mask_database_url(url: str) -> str:
    """Mask the password inside a database URL for safe logging."""
    return _DB_CREDENTIALS_RE.sub(r"\1***\2", url or "")


def database_host_port(url: str) -> Tuple[str, int]:
    """Extract (host, port) from a database URL (driver-agnostic)."""
    from urllib.parse import urlparse

    normalized = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    parsed = urlparse(normalized)
    return parsed.hostname or "", parsed.port or 5432
