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
        raw = self.telegram_channels.strip()
        try:
            value = json.loads(raw)
            if isinstance(value, dict): value = [value]
            if not isinstance(value, list): raise ValueError('TELEGRAM_CHANNELS JSON must be an array or object')
            return value
        except json.JSONDecodeError:
            # Also accept a simple Railway value such as: -1001,-1002,-1003
            # or one channel ID per line.
            values = [x.strip() for x in raw.replace('\\n', ',').replace('\\r', ',').split(',') if x.strip()]
            if values and all(x.lstrip('-').isdigit() for x in values):
                return [{'id': int(x)} for x in values]
            raise ValueError('TELEGRAM_CHANNELS must be valid JSON, e.g. [{"id":"-100123"}], or comma-separated numeric IDs')
    @property
    def keys(self):
        return [x.strip() for x in self.tmdb_api_keys.split(',') if x.strip()]

settings = Settings()
