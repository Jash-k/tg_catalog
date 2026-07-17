import json
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite+aiosqlite:///./data/addon.db'
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_string: str
    telegram_channels: str = '[]'
    tmdb_api_keys: str
    tmdb_language: str = 'en-US'
    scan_interval_hours: float = 4
    tmdb_refresh_interval_hours: float = 72
    max_messages_per_channel: int = 0
    min_match_confidence: float = .58
    page_size: int = 100

    @property
    def channels(self):
        value = json.loads(self.telegram_channels)
        if isinstance(value, dict):
            value = [value]
        return value
    @property
    def keys(self):
        return [x.strip() for x in self.tmdb_api_keys.split(',') if x.strip()]

settings = Settings()
