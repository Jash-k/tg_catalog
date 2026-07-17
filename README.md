# Telegram → TMDB → Stremio catalog addon

A metadata-only Stremio addon. It scans Telegram media documents/captions with Telethon, normalizes release names, matches TMDB, and exposes six browsable catalogs. It never stores file links, Telegram message IDs, quality tags, or file sizes. It does not provide streams; Stremio can use other installed addons for playback.

## Catalogs

- **Tamil Movies** — original Tamil films from configured Tamil original channels
- **Dubbed Movies** — films whose filename/caption contains a Tamil audio/dub tag
- **Tamil Series** — series from Tamil, Telugu, Malayalam, and Kannada channels; Tamil-channel series are listed first by the application’s catalog convention
- **Other Movies** — matched movie fallback for non-South-Indian channels and South-Indian movies not otherwise eligible
- **Other Series** — series from non-South-Indian channels
- **Anime** — Japanese animated content, independent of movie/series type

`TELEGRAM_CHANNELS` is JSON. Example:

```json
[
  {"username":"tamil_originals", "category":"tamil", "original":true},
  {"username":"tamil_dubbed", "category":"other", "original":false},
  {"username":"telugu_series", "category":"telugu", "original":true},
  {"username":"anime_channel", "category":"anime", "original":false}
]
```

`category` must be `tamil`, `telugu`, `malayalam`, `kannada`, `other`, or `anime`. The channel username may also be a numeric Telegram channel ID if the deployment account can resolve it.

## Telegram session string

Create the session string once on a trusted machine, where phone login is possible:

```bash
pip install telethon
python - <<'PY'
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
api_id = int(input('API ID: ')); api_hash = input('API hash: ')
with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
PY
```

Put the printed value in Railway as `TELEGRAM_SESSION_STRING`. Keep it secret. The production container uses this session and never runs a phone-login flow.

## Local run

```bash
cp .env.example .env
# edit .env
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Add to Stremio: `http://localhost:8000/manifest.json`.

## Railway / Docker

1. Create a Railway project and deploy this repository as a Docker service.
2. Add all variables in `.env.example` as Railway variables. `PORT` is supplied by Railway and is used by the Docker command.
3. Use a Railway Postgres database for persistence and set `DATABASE_URL` to its async SQLAlchemy URL, e.g. `postgresql+asyncpg://...`.
4. Add a persistent volume if using SQLite (mount it at `/app/data`); Postgres is recommended.
5. After deployment, add `https://YOUR-DOMAIN/manifest.json` in Stremio.

The scheduler performs the first full historical scan immediately when the database has no content, then scans every `SCAN_INTERVAL_HOURS`. Since Telegram message IDs are intentionally not persisted, scheduled scans are idempotent metadata upserts. TMDB metadata is refreshed every `TMDB_REFRESH_INTERVAL_HOURS`. Multiple TMDB keys are comma-separated and rotated when a key is rate-limited or fails.

## Data model and matching

`content` has one row per `(tmdb_id, media_type)`, with a season-number list for series. `unmatched` stores low-confidence names separately for review. A filename is cleaned by removing channel watermarks, release/codec/quality tags, audio tags, and season/episode markers; years are extracted before leet normalization so four-digit years are preserved. Movies are deduplicated by TMDB ID. Series deduplicate by TMDB ID and union available season numbers, so episode files and season batches produce the same catalog result.

## Notes

- TMDB API usage and Telegram access must comply with their terms and the rights applicable to your channels/content.
- The implementation intentionally does not expose stream URLs or Telegram media.
- Before production, pin a Railway volume/Postgres backup and rotate any exposed session string or API key.
