"""Application configuration loaded from environment variables.

Uses pydantic-settings. All values can be overridden with real
environment variables (Railway) or a local ``.env`` file.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/tamilcatalog"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = DEFAULT_DATABASE_URL
    # Railway Postgres plugin component variables. When DATABASE_URL is
    # missing or still the .env.example placeholder, the full URL is
    # composed from these automatically.
    PGHOST: str = ""
    PGPORT: str = ""
    PGUSER: str = ""
    PGPASSWORD: str = ""
    PGDATABASE: str = ""

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------
    # NOTE: TELEGRAM_API_ID is a plain string here so empty/placeholder
    # values in .env never crash validation; use `telegram_api_id_int`.
    TELEGRAM_API_ID: str = ""
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION_STRING: str = ""
    TELEGRAM_CHANNELS: str = ""

    # ------------------------------------------------------------------
    # TMDB
    # ------------------------------------------------------------------
    # TMDB_API_KEY may hold ONE key or several comma-separated keys.
    # TMDB_API_KEYS is an optional extra comma-separated pool that gets
    # merged in. Keys rotate round-robin; a key that is rejected (401) is
    # parked for a while, a 429 rotates to the next key immediately.
    TMDB_API_KEY: str = ""
    TMDB_API_KEYS: str = ""
    TMDB_ACCESS_TOKEN: str = ""
    TMDB_BASE_URL: str = "https://api.themoviedb.org/3"
    TMDB_IMAGE_BASE_URL: str = "https://image.tmdb.org/t/p"
    # How long a failed (HTTP 401) TMDB key stays parked before a retry
    TMDB_KEY_DISABLE_SECONDS: float = 1800.0

    # ------------------------------------------------------------------
    # App
    # ------------------------------------------------------------------
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    API_VERSION: str = "v1"

    # ------------------------------------------------------------------
    # Scanner settings
    # ------------------------------------------------------------------
    SCAN_INTERVAL_HOURS: int = 6
    TMDB_SYNC_INTERVAL_DAYS: int = 7
    BATCH_SIZE: int = 100
    TMDB_RATE_LIMIT_DELAY: float = 0.25
    MIN_CONFIDENCE_SCORE: int = 70

    # ------------------------------------------------------------------
    # Security / CORS
    # ------------------------------------------------------------------
    API_SECRET_KEY: str = ""
    ALLOWED_ORIGINS: str = "*"

    # ------------------------------------------------------------------
    # Validators / derived helpers
    # ------------------------------------------------------------------
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def _normalize_db_url(cls, value: object) -> object:
        """Accept Railway-style postgres:// URLs and force the asyncpg driver."""
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            value = "postgresql://" + value[len("postgres://"):]
        if value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://"):]
        return value

    @model_validator(mode="after")
    def _compose_db_url_from_pg_vars(self) -> "Settings":
        """Compose DATABASE_URL from Railway's PG* component variables.

        Kicks in when DATABASE_URL was never provided (still the localhost
        default and DATABASE_URL is absent from the environment) or when it
        is literally the .env.example placeholder (...@host:5432/...).
        """
        env_has_url = bool(os.environ.get("DATABASE_URL"))
        is_placeholder = "user:pass@host" in (self.DATABASE_URL or "")
        is_default = (not env_has_url) and self.DATABASE_URL == DEFAULT_DATABASE_URL
        if (is_placeholder or is_default) and self.PGHOST:
            port = self.PGPORT or "5432"
            user = self.PGUSER or "postgres"
            db = self.PGDATABASE or "postgres"
            self.DATABASE_URL = (
                f"postgresql+asyncpg://{user}:{self.PGPASSWORD}@{self.PGHOST}:{port}/{db}"
            )
        return self

    @property
    def telegram_api_id_int(self) -> Optional[int]:
        try:
            return int(self.TELEGRAM_API_ID)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_channel_ref(ref: str) -> str:
        """Accept usernames, @handles, t.me links, and -100... channel IDs."""
        ref = ref.strip()
        # https://t.me/somechannel or t.me/somechannel -> somechannel
        ref = re.sub(
            r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/", "", ref, flags=re.IGNORECASE
        )
        ref = ref.lstrip("@").strip().split("/")[0]
        return ref

    @property
    def telegram_channels_list(self) -> List[str]:
        refs: List[str] = []
        for raw in self.TELEGRAM_CHANNELS.split(","):
            ref = self._normalize_channel_ref(raw)
            if ref:
                refs.append(ref)
        return refs

    @property
    def telegram_configured(self) -> bool:
        return bool(
            self.telegram_api_id_int
            and self.TELEGRAM_API_HASH
            and self.TELEGRAM_SESSION_STRING
        )

    @property
    def tmdb_api_keys(self) -> List[str]:
        """All configured TMDB v3 API keys (unique, order-preserving)."""
        keys: List[str] = []
        for raw in (self.TMDB_API_KEY, self.TMDB_API_KEYS):
            for part in str(raw).split(","):
                key = part.strip()
                if key and key not in keys:
                    keys.append(key)
        return keys

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_api_keys or self.TMDB_ACCESS_TOKEN)

    @property
    def allowed_origins_list(self) -> List[str]:
        if self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def api_secret_key_or_none(self) -> Optional[str]:
        return self.API_SECRET_KEY or None

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
