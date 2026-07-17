"""Filename cleaning pipeline.

Transforms messy release filenames such as::

    [TamilMV] Ponniyin.Selvan.Part.1.(2022).Tamil.1080p.WEB-DL.x264.AAC.mkv

into a structured result::

    {"cleaned_title": "Ponniyin Selvan Part 1", "detected_year": 2022, ...}

Pipeline order: language/anime/type flags are DETECTED on the raw string
first (before any tokens are removed); then season/episode tokens, channel
tags, URLs, quality tags, language tags, leet-speak fixes, year extraction,
part handling, separator normalization and final cleanup.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

from app.utils.helpers import VIDEO_EXTENSIONS

# ---------------------------------------------------------------------------
# Regex building blocks
# ---------------------------------------------------------------------------

YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-2]\d)\b")

# Step 1 - language/audio tags (checked BEFORE cleaning)
LANGUAGE_TAG_PATTERNS = {
    "Tamil": [
        r"tamil\s*dub(?:bed)?",
        r"tamil\s*audio",
        r"[\[\(\{]\s*tamil\s*[\]\)\}]",
        r"\btamil\b",
    ],
    "Telugu": [r"\btelugu\b", r"\btel\b"],
    "Hindi": [r"\bhindi\b", r"\bhin\b"],
    "Malayalam": [r"\bmalayalam\b", r"\bmal\b"],
    "Kannada": [r"\bkannada\b", r"\bkan\b"],
    "Multi": [r"\bmulti\b", r"multi[\s.\-]*audio", r"dual[\s.\-]*audio"],
}

# Step 2 - anime indicators (checked BEFORE cleaning)
_ANIME_PATTERNS = [
    r"\banime\b",
    r"crunchyroll",
    r"funimation",
    r"subindo",
    r"webrip[\s.\-]?varyg",
]
_ANIME_GROUP_PATTERNS = [
    r"subsplease",
    r"erai[\s.\-]?raws",
    r"horriblesubs",
    r"yuisubs",
    r"judas",
]
_JAPANESE_CHARS_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")

# Step 3 - content type / season detection (order matters!)
_EPISODE_RANGE_RE = re.compile(
    r"\bs(\d{1,2})\s*[.\-]?\s*e(\d{1,3})\s*[-–—]?\s*(?:e\s*)?(\d{1,3})\b", re.IGNORECASE
)
_COMPLETE_SEASON_RE = re.compile(
    r"\b(?:s(?:eason)?[\s.\-]?(\d{1,2})[\s.\-]?(?:complete|full)|complete[\s.\-]?season(?:[\s.\-]?(\d{1,2}))?)\b",
    re.IGNORECASE,
)
_SINGLE_EPISODE_RE = re.compile(r"\bs(\d{1,2})\s*[.\-]?\s*e(\d{1,4})\b", re.IGNORECASE)
_ALT_EPISODE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})\b")
_SEASON_TOKEN_RE = re.compile(r"\bs(\d{1,2})\b", re.IGNORECASE)
_SEASON_WORD_RE = re.compile(r"\bseason[\s.\-]?(\d{1,2})\b", re.IGNORECASE)
_EPISODE_WORD_RE = re.compile(r"\b(?:episode|ep)[\s.\-]?(\d{1,3})\b", re.IGNORECASE)
_MOVIE_KEYWORD_RE = re.compile(r"\bmovie\b", re.IGNORECASE)

# Step 4 - channel tags / watermarks / URLs
_KNOWN_CHANNELS = [
    "1tamilmv", "tamilmv", "tamilblasters", "tamilblaster", "tamilrockers",
    "tamilgun", "tamilyogi", "moviesmafia", "isaidub", "tamildbox",
    "tamilprime", "katmoviehd", "teamtr", "properfix",
]
_CHANNEL_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(_KNOWN_CHANNELS) + r"|tmv|bwt)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_LEADING_TAG_RE = re.compile(r"^\s*(?:\[([^\]]{0,80})\]|\(([^)]{0,80})\)|\{([^}]{0,80})\})")
_HANDLE_RE = re.compile(r"@\w+")
_URL_RES = [
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"t\.me/\S+", re.IGNORECASE),
    re.compile(r"telegram\.me/\S+", re.IGNORECASE),
    re.compile(r"www\.\S+", re.IGNORECASE),
]

# Step 5 - technical quality tags
_QUALITY_TERMS = [
    # resolution
    r"2160p", r"1080p", r"720p", r"540p", r"576p", r"480p",
    r"4k", r"uhd", r"qhd", r"fhd", r"hd", r"sd",
    # source
    r"hd[\s.\-]?rip", r"web[\s.\-]?dl", r"web[\s.\-]?rip", r"web",
    r"blu[\s.\-]?ray", r"br[\s.\-]?rip", r"bd[\s.\-]?rip",
    r"dvd[\s.\-]?rip", r"dvd[\s.\-]?scr", r"dvd", r"hdtv", r"pdvd", r"pre[\s.\-]?dvd",
    r"hdcam", r"cam[\s.\-]?rip", r"cam", r"hq", r"true",
    # video codec
    r"x264", r"x265", r"h\s?[.\-]?\s?264", r"h\s?[.\-]?\s?265", r"hevc", r"avc",
    r"vp9", r"xvid", r"divx",
    # audio
    r"aac\s?2\s?[.\-]?\s?0", r"aac", r"ddp\s?5\s?[.\-]?\s?1", r"dd\s?5\s?[.\-]?\s?1",
    r"ddp\s?2\s?[.\-]?\s?0", r"ddp", r"dd", r"eac3", r"ac3", r"dts", r"atmos",
    r"true[\s.\-]?hd", r"flac", r"mp3",
    r"esub", r"subs", r"multi[\s.\-]?audio", r"multi[\s.\-]?audios",
    # hdr
    r"hdr10(?:\+|plus)?", r"hdr", r"dolby[\s.\-]?vision", r"sdr", r"hlg",
    # misc
    r"proper", r"repack", r"retail", r"remux", r"extended", r"theatrical",
    r"unrated", r"director'?s?[\s.\-]?cut", r"i[\s.\-]?ta",
    # streaming services
    r"nf", r"amzn", r"dsnp", r"atvp", r"hmax", r"zee5", r"sony[\s.\-]?liv",
    r"hotstar", r"netflix", r"amazon",
]
_QUALITY_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(_QUALITY_TERMS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_AUDIO_CHANNEL_RE = re.compile(r"(?<![A-Za-z0-9])(?:2\.0|5\.1|7\.1)(?![A-Za-z0-9])")
_SIZE_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s?(?:mb|gb|tb|kb)(?![A-Za-z0-9])", re.IGNORECASE)

# Bracketed groups containing junk keywords are removed wholesale.
_BRACKET_JUNK_KEYWORDS = re.compile(
    r"audio|esub|sub|dub|tamil|anime|tamilmv|tamilblasters|tamilrockers|tamilgun|"
    r"tamilyogi|isaidub|katmoviehd|subsplease|erai|horriblesubs|judas|webrip|"
    r"web[\s.\-]?dl|hdrip|hq|www\.|t\.me",
    re.IGNORECASE,
)

_EXTENSION_RE = re.compile(
    r"\.(" + "|".join(ext.lstrip(".") for ext in VIDEO_EXTENSIONS) + r")$",
    re.IGNORECASE,
)

# Tokens that are stripped AFTER detection (positional / structural stripping)
_LANG_ALT = (
    r"tamil\s*dub(?:bed)?|tamil\s*audio|tamil|telugu|tel|hindi|hin|malayalam|mal|"
    r"kannada|kan|dubbed|dubs?|multi[\s.\-]*audio|dual[\s.\-]*audio|multi"
)
_LANG_TOKEN = rf"(?:(?<![A-Za-z0-9])(?:{_LANG_ALT})(?![A-Za-z0-9]))"
_SEPARATOR = r"(?:[\s,+&]|(?<![A-Za-z0-9])and(?![A-Za-z0-9]))"
_TRAILING_TAG_RUN_RE = re.compile(
    rf"{_SEPARATOR}*{_LANG_TOKEN}{_SEPARATOR}*$",
    re.IGNORECASE,
)
# Language lists ("Telugu + Tamil", "Tamil Telugu Hindi") - requires at
# least two language tokens, so real titles such as "Hindi Medium" that
# merely begin with a language word are never mangled.
_LANG_LIST_RE = re.compile(
    rf"{_LANG_TOKEN}(?:{_SEPARATOR}+{_LANG_TOKEN})+{_SEPARATOR}*",
    re.IGNORECASE,
)
_LEADING_LANG_LIST_RE = re.compile(rf"^{_SEPARATOR}*{_LANG_LIST_RE.pattern}", re.IGNORECASE)
# Dub / anime markers are structural noise and safe to remove anywhere.
_ANYWHERE_TAG_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:dubbed|dubs?|tamil\s*dub(?:bed)?|tamil\s*audio|anime|"
    r"crunchyroll|funimation|subindo|varyg)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TRAILING_SERIES_WORD_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:complete|finale|final|batch)(?![A-Za-z0-9])\s*$", re.IGNORECASE
)
_TRAILING_JUNK_CHARS_RE = re.compile(r"[\s,+\-&.]+$")
_LEADING_JUNK_CHARS_RE = re.compile(r"^[\s,+\-&.]+")

_SINGLE_ALPHA_RE = re.compile(r"(?<![A-Za-z0-9])(?![AaIi](?![A-Za-z0-9]))[A-Za-z](?![A-Za-z0-9])")
_DANGLING_TAIL_RE = re.compile(r"[\s\-]*\b(season|part|vol|volume)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class CleanedResult:
    cleaned_title: Optional[str]
    detected_year: Optional[int] = None
    content_type: str = "movie"
    season_number: Optional[int] = None
    is_tamil_audio: bool = False
    is_anime_likely: bool = False
    detected_languages: List[str] = field(default_factory=list)
    confidence: str = "low"  # high | medium | low
    parse_failed: bool = False
    failure_reason: Optional[str] = None
    original_filename: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class CleaningPipeline:
    """Sequential filename -> title cleaning pipeline."""

    # -- Step 1 -------------------------------------------------------------
    @staticmethod
    def _detect_languages(text: str) -> List[str]:
        lowered = text.lower()
        found: List[str] = []
        for language, patterns in LANGUAGE_TAG_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, lowered, re.IGNORECASE):
                    found.append(language)
                    break
        return found

    # -- Step 2 -------------------------------------------------------------
    @staticmethod
    def _detect_anime(text: str) -> bool:
        lowered = text.lower()
        if any(re.search(p, lowered) for p in _ANIME_PATTERNS):
            return True
        if any(re.search(p, lowered) for p in _ANIME_GROUP_PATTERNS):
            return True
        return bool(_JAPANESE_CHARS_RE.search(text))

    # -- Step 3 -------------------------------------------------------------
    @staticmethod
    def _first_season_token(text: str) -> Optional[int]:
        m = _SEASON_TOKEN_RE.search(text)
        if m:
            return int(m.group(1))
        m = _SEASON_WORD_RE.search(text)
        if m:
            return int(m.group(1))
        return None

    def _detect_type_and_season(self, text: str) -> Tuple[str, Optional[int]]:
        # Episode ranges: S01E01-E08, S01E01E02
        m = _EPISODE_RANGE_RE.search(text)
        if m:
            return "series", int(m.group(1))
        # Full season: S01.Complete, Season.1.Complete, "Complete Season"
        if _COMPLETE_SEASON_RE.search(text):
            return "series", self._first_season_token(text)
        # Individual episodes: S01E05, s01e05, 1x05, S01.E05
        m = _SINGLE_EPISODE_RE.search(text)
        if m:
            return "series", int(m.group(1))
        m = _ALT_EPISODE_RE.search(text)
        if m:
            return "series", int(m.group(1))
        m = _EPISODE_WORD_RE.search(text)
        if m:
            # "Episode 5" type files: series, season unknown
            return "series", None
        # Season only: S01, S1, Season.1, Season01
        m = _SEASON_TOKEN_RE.search(text)
        if m:
            return "series", int(m.group(1))
        m = _SEASON_WORD_RE.search(text)
        if m:
            return "series", int(m.group(1))
        # Explicit "Movie" keyword, otherwise default movie.
        return "movie", None

    @staticmethod
    def _remove_season_tokens(text: str) -> str:
        """Strip the season/episode markers themselves from the working title."""
        text = _EPISODE_RANGE_RE.sub(" ", text)
        text = _COMPLETE_SEASON_RE.sub(" ", text)
        text = _SINGLE_EPISODE_RE.sub(" ", text)
        text = _ALT_EPISODE_RE.sub(" ", text)
        text = _EPISODE_WORD_RE.sub(" ", text)
        text = _SEASON_TOKEN_RE.sub(" ", text)
        text = _SEASON_WORD_RE.sub(" ", text)
        text = _TRAILING_SERIES_WORD_RE.sub("", text)
        return text

    # -- Step 4 -------------------------------------------------------------
    @staticmethod
    def _strip_channel_tags(text: str) -> str:
        # URLs and @handles first (before channel names break the tokens).
        text = _HANDLE_RE.sub(" ", text)
        for url_re in _URL_RES:
            text = url_re.sub(" ", text)

        # Strip leading bracketed tags, but preserve a leading "(2022)" year.
        while True:
            m = _LEADING_TAG_RE.match(text)
            if not m:
                break
            inner = next((g for g in m.groups() if g is not None), "") or ""
            if YEAR_RE.fullmatch(inner.strip()):
                break  # keep leading year
            text = text[m.end():]

        # Remove bracketed groups anywhere that contain junk keywords.
        def _drop_junk_brackets(match: re.Match) -> str:
            return " " if _BRACKET_JUNK_KEYWORDS.search(match.group(0)) else match.group(0)

        text = re.sub(r"\[[^\]]{0,80}\]|\([^)]{0,80}\)|\{[^}]{0,80}\}", _drop_junk_brackets, text)
        # Flatten any remaining bracket characters (mostly around the year).
        text = re.sub(r"[\[\]{}()]", " ", text)
        # Remove known channel names.
        text = _CHANNEL_NAME_RE.sub(" ", text)
        return text

    # -- Step 5 -------------------------------------------------------------
    @staticmethod
    def _strip_quality_tags(text: str) -> str:
        text = _SIZE_RE.sub(" ", text)
        text = _QUALITY_RE.sub(" ", text)
        text = _AUDIO_CHANNEL_RE.sub(" ", text)
        return text

    # -- Step 5b: language/dub tag removal (positional, title-safe) ---------
    @staticmethod
    def _strip_language_tokens(text: str) -> str:
        # Dub/anime markers are never part of real titles: remove anywhere.
        text = _ANYWHERE_TAG_RE.sub(" ", text)
        # Language-tag lists need 2+ tokens: "Telugu + Tamil", "Tamil Telugu".
        # A single leading word like "Hindi" in "Hindi Medium" is NOT touched.
        text = _LANG_LIST_RE.sub(" ", text)
        # Strip trailing language-tag runs (tags live after the title/year,
        # before the quality junk): "Vikram ... Tamil" -> "Vikram ...".
        previous = None
        while previous != text:
            previous = text
            text = _TRAILING_JUNK_CHARS_RE.sub("", text)
            text = _TRAILING_TAG_RUN_RE.sub("", text)
        return text

    # -- Step 6 -------------------------------------------------------------
    @staticmethod
    def _fix_leet(text: str) -> str:
        # Detect and temporarily remove the year first (e.g. 2022),
        # then apply leet substitutions, then re-insert the year.
        year_match = YEAR_RE.search(text)
        placeholder = " YRPLACEHOLDERX "
        saved_year = None
        if year_match:
            saved_year = year_match.group(1)
            text = YEAR_RE.sub(placeholder, text, count=1)

        # Symbol substitutions anywhere.
        text = text.replace("@", "a").replace("!", "i").replace("$", "s")
        # Digit substitutions only when the digit is surrounded by letters,
        # so sequel numbers ("Part 3") and anything numeric survive.
        text = re.sub(r"(?<=[A-Za-z])0(?=[A-Za-z])", "o", text)
        text = re.sub(r"(?<=[A-Za-z])3(?=[A-Za-z])", "e", text)
        text = re.sub(r"(?<=[A-Za-z])1(?=[A-Za-z])", "i", text)
        text = re.sub(r"(?<=[A-Za-z])5(?=[A-Za-z])", "s", text)

        if saved_year is not None:
            text = text.replace(placeholder.strip(), saved_year)
        return text

    # -- Step 8 / 10 helpers ------------------------------------------------
    @staticmethod
    def _smart_title_case(text: str) -> str:
        words = []
        for word in text.split():
            if word.islower():
                words.append(word.capitalize())
            elif word.isupper() and len(word) > 4:
                # JAILER -> Jailer, but keep short acronyms: RRR, KGF, LEO
                words.append(word.capitalize())
            else:
                words.append(word)  # mixed-case / short acronyms preserved
        return " ".join(words)

    # -- Main entry ----------------------------------------------------------
    def clean(self, raw: Optional[str]) -> CleanedResult:
        if not raw or not raw.strip():
            return CleanedResult(
                cleaned_title=None,
                parse_failed=True,
                failure_reason="empty_filename",
                original_filename=raw,
            )

        work = unicodedata.normalize("NFKC", raw.strip())
        work = _EXTENSION_RE.sub("", work)  # drop .mkv / .mp4 / ... suffix

        # Steps 1-3 run on the raw string BEFORE anything is removed.
        detected_languages = self._detect_languages(work)
        is_tamil_audio = "Tamil" in detected_languages
        is_anime_likely = self._detect_anime(work)
        content_type, season_number = self._detect_type_and_season(work)

        # If hyphens are clearly the main separator, treat them like dots.
        probe = work.strip()
        if ("." not in probe) and ("_" not in probe) and (" " not in probe) and ("-" in probe):
            work = work.replace("-", " ")

        # Step 4 - channel tags / watermarks / URLs
        work = self._strip_channel_tags(work)

        # Season/episode markers carry no title information anymore.
        work = self._remove_season_tokens(work)

        # Step 5 - quality tags and sizes
        work = self._strip_quality_tags(work)

        # Language/dub tags have served their purpose as flags.
        work = self._strip_language_tokens(work)

        # Step 6 - leet-speak fix (year-aware)
        work = self._fix_leet(work)

        # Step 7 - year extraction
        detected_year: Optional[int] = None
        year_match = YEAR_RE.search(work)
        if year_match:
            detected_year = int(year_match.group(1))
            work = YEAR_RE.sub(" ", work, count=1)

        # Step 8 - part indicators are kept (context-aware):
        # "Part 1", "Part II", "Vol 1" that survive quality-tag stripping are
        # almost always part of the real title, so we leave them in place.
        # Dangling/leading part markers are dropped by the tail cleanup below.

        # Step 9 - separator normalization
        work = re.sub(r"[._/\\|~·•]", " ", work)
        work = re.sub(r"[,!?:;\"'`]", " ", work)
        work = re.sub(r"\s*\+\s*", " ", work)
        work = re.sub(r"-\s+|\s+-|-{2,}", " ", work)  # hyphens used as separators
        work = re.sub(r"\s+", " ", work).strip(" -/&")

        # Step 10 - final cleanup
        work = _SINGLE_ALPHA_RE.sub(" ", work)  # stray single letters (keep A / I)
        work = _DANGLING_TAIL_RE.sub("", work)
        work = _TRAILING_SERIES_WORD_RE.sub("", work)
        work = re.sub(r"\s+", " ", work).strip(" -/&")
        title = self._smart_title_case(work)

        letters = sum(ch.isalpha() for ch in title)
        if len(title) < 2 or letters == 0:
            return CleanedResult(
                cleaned_title=title or None,
                detected_year=detected_year,
                content_type=content_type,
                season_number=season_number,
                is_tamil_audio=is_tamil_audio,
                is_anime_likely=is_anime_likely,
                detected_languages=detected_languages,
                confidence="low",
                parse_failed=True,
                failure_reason="parse_failed",
                original_filename=raw,
            )

        alpha_ratio = letters / max(len(title), 1)
        if detected_year is not None and len(title) >= 3 and alpha_ratio >= 0.5:
            confidence = "high"
        elif len(title) >= 3 and alpha_ratio >= 0.5:
            confidence = "medium"
        else:
            confidence = "low"

        return CleanedResult(
            cleaned_title=title,
            detected_year=detected_year,
            content_type=content_type,
            season_number=season_number,
            is_tamil_audio=is_tamil_audio,
            is_anime_likely=is_anime_likely,
            detected_languages=detected_languages,
            confidence=confidence,
            parse_failed=False,
            original_filename=raw,
        )
