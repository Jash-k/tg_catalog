# Tamil Content Catalog

A private, self-hosted **metadata catalog** that scans configured Telegram
channels for movie/series filenames, cleans them, matches them against TMDB,
and exposes a rich REST API for browsing, filtering, and discovery.

> **Metadata only.** The catalog stores TMDB metadata (titles, posters, cast,
> ratings, seasons) — **no file links, no message IDs, no file hashes, no
> quality info**. Nothing is hosted, proxied, or downloadable.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 1. Project Overview

```
Telegram channels ──▶ Scanner (Telethon) ──▶ CleaningPipeline (filename → title)
        │                                        │
        │                                        ▼
        │                              TMDBService (search, confidence scoring ≥ 70)
        │                                        │
        │                                        ▼
        │                        classify_content() ──▶ one of 6 catalogs
        │                                        │
        ▼                                        ▼
scan_tracker / unmatched_items        catalog_items (PostgreSQL + tsvector FTS)
                                                 │
                                                 ▼
                              FastAPI REST API (/api/v1) + APScheduler jobs
```

**Tech stack:** Python 3.11+, FastAPI + uvicorn, PostgreSQL 15 (asyncpg +
SQLAlchemy 2.0 async + Alembic), Telethon, httpx, rapidfuzz, APScheduler,
Pydantic v2, PostgreSQL full-text search (tsvector) + pg_trgm, Docker,
Railway.app single-service deployment.

**Async throughout** — database, TMDB, and Telegram I/O are all `async/await`.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 2. Catalog Types

Every item belongs to **exactly one** of 6 catalogs, assigned by
`classify_content()` in strict priority order:

| # | Key | Rule | Examples |
|---|-----|------|----------|
| 1 | `anime` *(checked first)* | `original_language = ja` AND (genre `Animation` OR anime keywords in filename) | Demon Slayer (Tamil dub), One Piece, Studio Ghibli films |
| 2 | `tamil_movies` | movie, `ta` original, not dubbed | Vikram, Leo, Jailer, Ponniyin Selvan |
| 3 | `dubbed_movies` | movie, found with Tamil audio but originally another language | RRR (Tamil), KGF (Tamil), Avatar (Tamil) |
| 4 | `tamil_series` | series, South-Indian original (`ta/te/ml/kn`) with Tamil audio or Tamil-original | Suzhal (Tamil original) |
| 5 | `other_movies` | movie, non-South-Indian original (catch-all for remaining movies) | Hollywood films, Korean films, original Hindi films |
| 6 | `other_series` | series, non-South-Indian original (catch-all for remaining series) | Breaking Bad, Money Heist, K-dramas |

**Notes**

- *Anime wins over everything* for Japanese animated content.
- **Tamil Series sort priority:** items with `is_tamil_original = true`
  (original language `ta`) always appear **before** other South Indian
  series in `tamil_series` responses, regardless of the chosen sort field.
- Classification follows the language rules strictly (South Indian =
  `ta, te, ml, kn`).
- The weekly TMDB sync job can **reclassify** items when better metadata
  appears (see `updated_at` / logs).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 3. Prerequisites

| Requirement | Where to get it |
|---|---|
| Python 3.11+ / Docker | For local runs, `docker compose` covers everything |
| PostgreSQL 15 | docker-compose (local) or Railway plugin (prod) |
| Telegram API credentials | <https://my.telegram.org/apps> → create an app → `api_id`, `api_hash` |
| Telegram account in the target channels | The scanner reads channels as *your* user account |
| TMDB API key | <https://www.themoviedb.org/settings/api> → API key (v3) and/or Read Access Token (v4) |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 4. Local Setup (step by step)

### 4.1 Clone and configure

```bash
cd tamil-catalog
cp .env.example .env
```

Edit `.env` (see section 5 for Telegram, and fill `TMDB_API_KEY`).

### 4.2 Start with Docker Compose (recommended)

```bash
docker compose up --build
```

- Postgres 15 starts with a healthcheck; the API waits for it.
- Migrations run automatically (`alembic upgrade head` in `startup.sh`).
- API docs: <http://localhost:8000/docs>

> `docker-compose.yml` overrides `DATABASE_URL` to reach the `db` service.
> If you run the API on the host instead, point `DATABASE_URL` at
> `postgresql+asyncpg://postgres:postgres@localhost:5432/tamilcatalog`.

### 4.3 Run without Docker (host Python)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/tamilcatalog"
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4.4 One-time historical scan (optional, usually automatic)

On first startup with empty `scan_tracker`, the app triggers a full
historical scan in the background automatically. To run it manually:

```bash
python scripts/initial_scan.py
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 5. Generating Telegram Session String — CRITICAL

Railway (and any headless cloud box) **cannot do phone-based interactive
login**. You must generate a **session string locally once**, then paste it
into the `TELEGRAM_SESSION_STRING` env var.

```bash
# 1. Fill TELEGRAM_API_ID + TELEGRAM_API_HASH in your local .env
# 2. Run the generator LOCALLY (not on Railway):
python scripts/generate_session.py
```

The script will:

1. Ask for your phone number (international format, e.g. `+919876543210`).
2. Ask for the **login code** Telegram sends you.
3. Ask for your **2FA password**, if enabled.
4. Print a long `SESSION_STRING`.

```
======================================================================
SUCCESS. Copy this SESSION_STRING into Railway env vars:
======================================================================
1BAAADQAQ...very...long...string
```

- Copy it **exactly** (no extra whitespace) into
  `TELEGRAM_SESSION_STRING`.
- Treat it like a password — it grants full access to your Telegram
  account. Never commit it.
- If the session is revoked or expires, the API logs
  `Telegram session is not authorized` — regenerate the string and update
  the variable.
- The account used **must already be a member of private channels** you
  want scanned; public channels work with username only.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 6. Railway Deployment (step by step)

### 6.1 Create the project

1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
3. Railway auto-detects the `Dockerfile` (`railway.toml` pins
   `builder = "DOCKERFILE"`).

### 6.2 Add PostgreSQL

1. In the project: **New → Database → Add PostgreSQL** (same project +
   environment as the app service).
2. Open your app service → **Variables** → **New Variable** → set
   `DATABASE_URL` to the **reference** `${{Postgres.DATABASE_URL}}`
   (use the variable-reference picker, don't paste the literal text).
   The app normalizes `postgresql://` URLs to `postgresql+asyncpg://`
   automatically.
3. **Alternative:** instead of `DATABASE_URL`, reference the component
   variables `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`
   individually — the app composes the full URL from them automatically
   (useful when `DATABASE_URL` is already taken by something else).
4. After the first deploy, confirm in the deploy logs that the
   `[startup] database target: ...` line shows your real Postgres host
   (password masked), and that migrations run to completion.

### 6.3 Set environment variables

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference) |
| `TELEGRAM_API_ID` | from my.telegram.org |
| `TELEGRAM_API_HASH` | from my.telegram.org |
| `TELEGRAM_SESSION_STRING` | from `scripts/generate_session.py` (section 5) |
| `TELEGRAM_CHANNELS` | `channel1,channel2,channel3` (no `@`) |
| `TMDB_API_KEY` and/or `TMDB_ACCESS_TOKEN` | from TMDB (multiple keys: comma-separate `TMDB_API_KEY`) |
| `APP_ENV` | `production` |
| `ALLOWED_ORIGINS` | your frontend origin(s) or `*` |
| `API_SECRET_KEY` | *(optional)* protect the API (send `X-API-Key`) |

### 6.4 Deploy & verify

- `railway.toml` runs `bash startup.sh` → `alembic upgrade head` →
  `uvicorn` on Railway's injected `$PORT`.
- Healthcheck: `GET /api/v1/health` (configured in `railway.toml`).
- Docs at `https://<your-app>.up.railway.app/docs`.
- On first boot with empty `scan_tracker`, a **full historical scan**
  starts automatically in the background; afterwards, incremental scans
  run every 6 hours.

```bash
curl https://<your-app>.up.railway.app/api/v1/health
# {"status":"healthy","database":"connected","telegram":"connected","tmdb":"connected",...}
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 7. Adding Telegram Channels

`TELEGRAM_CHANNELS` is a comma-separated list. Each entry may be a
**username**, an **@handle**, a **t.me link**, or a **numeric channel ID**:

```env
TELEGRAM_CHANNELS=tamilmoviesHD,@seriesworld,https://t.me/dubbedfilmz,-1001234567890
```

**Numeric IDs (`-100...`)**

- Use the full bot-style marked ID, e.g. `-1001234567890`.
- The session account **must be a member** of that channel (private
  channels have no public username, so membership is the only way in).
- On connect, the scanner warms Telethon's entity cache by loading your
  dialogs, which is what allows `-100...` IDs to resolve. If a channel
  logs `channel_not_found`, join it with the session account and restart.

**General**

- Usernames are case-insensitive; `t.me/xxx`, `@xxx`, and bare `xxx` are
  all equivalent.
- After changing the variable, redeploy. New channels are picked up by the
  next scheduled scan; a channel with no `scan_tracker` row is scanned
  **fully** (historical) the first time, then incrementally
  (`min_id = last_message_id`).
- Progress, run totals, and failures are tracked in `scan_tracker`
  (internal) and `unmatched_items` (review/debug).

### Multiple TMDB API keys

You can configure **several TMDB keys** — comma-separate them in
`TMDB_API_KEY` (and/or `TMDB_API_KEYS`):

```env
TMDB_API_KEY=key_one,key_two
TMDB_API_KEYS=key_three
```

- Keys are used **round-robin** across requests.
- On **429 (rate limit)** the client instantly rotates to the next key
  instead of sleeping (it still honors `Retry-After` when down to one key).
- On **401/403 (invalid/revoked key)** the key is parked for
  `TMDB_KEY_DISABLE_SECONDS` (default 1800) and automatically retried later.
- If `TMDB_ACCESS_TOKEN` (v4 read token) is set, it is used instead and
  key rotation is not needed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 8. API Reference

Base URL: `/api/v1` &nbsp;•&nbsp; Interactive docs: `/docs`, `/redoc`
&nbsp;•&nbsp; If `API_SECRET_KEY` is set, send header `X-API-Key: <value>`.

### Response envelope (list endpoints)

```json
{
  "success": true,
  "data": [ ... ],
  "meta": { "total": 500, "page": 1, "per_page": 24, "total_pages": 21, "catalog": "tamil_movies" }
}
```

Errors are enveloped too: `{ "success": false, "error": { "code": 404, "message": "..." } }`.

---

### `GET /api/v1/catalogs` — overview of all 6 catalogs

```json
{
  "catalogs": [
    {
      "key": "tamil_movies", "label": "Tamil Movies",
      "description": "Original Tamil language movies",
      "count": 245, "last_updated": "2024-01-15T10:30:00Z",
      "poster_samples": ["https://image.tmdb.org/t/p/w500/..."]
    }
  ]
}
```

### `GET /api/v1/catalogs/{key}` — browse one catalog

`key` ∈ `tamil_movies | dubbed_movies | tamil_series | other_movies | other_series | anime`

Query params: `page`, `per_page` (default 24, max 50),
`sort=added_at|rating|year|title` (default `added_at`),
`order=asc|desc` (default `desc`).

> `tamil_series` always orders `is_tamil_original DESC` **first**, then the
> requested sort.

### `GET /api/v1/discover` — rich combinable filtering

Every parameter is optional; see section 9 for examples.

| Param | Meaning |
|---|---|
| `catalog` | comma-separated catalog keys |
| `type` | `movie` / `series` |
| `genre`, `genre_mode` | comma-separated genres; `any` (OR, default) or `all` (AND) |
| `year`, `year_from`, `year_to`, `decade` | `2023`, `2020`, `2024`, `2020s` |
| `rating_min`, `rating_max` | `7.0` … `9.5` |
| `original_language` | comma-separated TMDB codes (`ta,te,hi,en,ja,ko`) |
| `is_dubbed`, `is_tamil_original`, `is_anime` | `true` / `false` |
| `director`, `cast` | partial, case-insensitive |
| `runtime_min`, `runtime_max` | minutes (movies) |
| `has_season` | `1,2` — series having those seasons |
| `added_after`, `added_before` | ISO dates `2024-01-01` |
| `sort` | `rating\|year\|title\|added_at\|vote_count\|runtime` |
| `order`, `page`, `per_page` | `asc\|desc`, `1`, `24` |

### `GET /api/v1/search?q=...` — full-text search

- `q` **required**, min 2 chars; optional `catalog`, `type`, `page`, `per_page`.
- Searches title (EN/TA/original), director, cast names (from JSON),
  overview via a weighted tsvector (trigger-maintained).
- Ranked by `ts_rank` + rating; matched terms highlighted with
  `ts_headline` (`<mark>...</mark>`) in `title_headline` / `overview_headline`.
- **Typo-tolerant fallback:** if FTS finds nothing, pg_trgm
  `similarity > 0.3` results are returned (`matched_via: "trigram"`).

### `GET /api/v1/items/{tmdb_id}` — item detail

Full metadata plus `similar`: 5 same-catalog items with overlapping genres.

### `GET /api/v1/filters` — filter metadata with counts

Optional `?catalog=tamil_movies` scopes genres/years/directors/etc. Returns
`genres`, `years`, `decades`, `directors`, `languages`, `catalog_counts`,
`rating_range`, `year_range`, `runtime_range`, `total_items`.

### `GET /api/v1/stats` — catalog statistics

Totals `by_catalog` / `by_type`, `recently_added` (10), `top_rated` (10),
`top_genres` (10), and `scanner_status` (channels,
`total_scanned_messages`, matched/unmatched, `last_scan_at`).

### `GET /api/v1/health` — probes

```json
{ "status": "healthy", "database": "connected", "telegram": "connected",
  "tmdb": "connected", "version": "1.0.0", "uptime_seconds": 3600 }
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 9. Discover API Filter Examples

```bash
# Top-rated Tamil movies from the 2020s
curl ".../api/v1/discover?catalog=tamil_movies&decade=2020s&rating_min=7.5&sort=rating&order=desc"

# Action AND Thriller movies (strict genre match)
curl ".../api/v1/discover?genre=Action,Thriller&genre_mode=all&type=movie"

# All Lokesh Kanagaraj films
curl ".../api/v1/discover?director=lokesh"

# Everything with Vijay Sethupathi in the cast
curl ".../api/v1/discover?cast=Vijay%20Sethupathi"

# Dubbed movies originally in Telugu or Kannada
curl ".../api/v1/discover?catalog=dubbed_movies&original_language=te,kn"

# Anime added in 2024
curl ".../api/v1/discover?catalog=anime&added_after=2024-01-01&added_before=2024-12-31"

# Series that have season 2 available
curl ".../api/v1/discover?type=series&has_season=2"

# Compact movies for a flight night: excellent, under 2h15m
curl ".../api/v1/discover?type=movie&rating_min=8&runtime_max=135&sort=vote_count&order=desc"

# Search with typo tolerance ("vikrm" finds Vikram)
curl ".../api/v1/search?q=vikrm"
```

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 10. Stremio Addon — how to get the URL

The app also speaks the **Stremio addon protocol** at the domain root, so
your six catalogs show up as browsable Stremio catalogs (posters, ratings,
overviews — still metadata only, **no streams**).

**Your addon URL is simply:**

```
https://<your-app>.up.railway.app/manifest.json
```

**Install it:**

1. Open Stremio → **Addons** (puzzle icon) → **Community Addons** search
   box → paste the manifest URL above → **Install**.
   *(On web.stremio.com: paste it into the "Add addon" field.)*
2. A **Tamil Catalog** addon appears with 7 catalogs:

   | Stremio catalog | Type | Backed by |
   |---|---|---|
   | Tamil Movies | movie | `tamil_movies` |
   | Dubbed Movies | movie | `dubbed_movies` |
   | Other Movies | movie | `other_movies` |
   | Anime - Movies | movie | `anime` (movies only) |
   | Tamil Series | series | `tamil_series` (Tamil originals first) |
   | Other Series | series | `other_series` |
   | Anime - Series | series | `anime` (series only) |

3. Browse them from Stremio's **Discover → Movies/Series** catalog picker.
   Search inside a catalog works too (full-text + typo tolerance).

**Streams:** the addon intentionally advertises only `catalog` + `meta`
resources. Stremio fills in streams from your other installed streaming
addons as usual. If Stremio can't fetch the addon, make sure
`ALLOWED_ORIGINS` isn't blocking cross-origin requests (default `*` works).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## 11. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Deploy logs show `socket.gaierror: [Errno -5] No address associated with hostname` during migrations | **DATABASE_URL points at an unresolvable host.** Most often: (1) the `.env.example` placeholder `...@host:5432/...` was copied into Railway variables, (2) the Postgres plugin isn't linked so the private hostname doesn't exist, or (3) a `*.railway.internal` hostname used from a different environment. Fix: add the Postgres plugin in the same project/environment, then set `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` as a **reference variable** (or link the `PG*` component variables — the app composes the URL automatically). Check the `[startup] database target: ...` line in deploy logs to see the exact (password-masked) host being used. Redeploy — `startup.sh` now waits up to 60s for the DB before migrating. |
| `GET /api/v1/health` says `database: disconnected` | Wrong/unreachable `DATABASE_URL`. On Railway, re-check the `${{Postgres.DATABASE_URL}}` reference. Locally, ensure Postgres is running (`docker compose up db`). |
| `telegram: not_configured` / scanner never runs | Missing `TELEGRAM_API_ID/HASH/SESSION_STRING`. Re-check variables; the API works without Telegram, only scanning is disabled. |
| Log: `Telegram session is not authorized` | Session string expired/revoked. Regenerate with `scripts/generate_session.py` and update `TELEGRAM_SESSION_STRING`. |
| Log: `Channel is private / not joined` | The session account isn't a member of that private channel. Join it with that account. |
| Log: `Channel not found / not resolvable` | A `-100...` ID requires the session account to be a member of the channel, and the global dialog cache must have loaded it (restart the app after joining). Double-check the exact digits. |
| Log: `TMDB rejected API key; parking it` | That key is invalid/revoked — it is skipped for `TMDB_KEY_DISABLE_SECONDS` while other keys keep working. Remove/replace it. |
| Log: `All TMDB API keys are parked/failed` | Every configured key failed. Check `TMDB_API_KEY` / `TMDB_API_KEYS` values, or configure `TMDB_ACCESS_TOKEN` instead. |
| Everything lands in `unmatched_items` with `no_tmdb_match` / `low_confidence` | Missing/invalid `TMDB_API_KEY`, or filenames are extremely mangled. Check `GET /api/v1/stats → scanner_status`, review `unmatched_items`, consider lowering `MIN_CONFIDENCE_SCORE` (default 70). |
| Frequent `FloodWaitError` logs | Telegram rate limiting; the scanner already paces batches (100 msgs + 1s sleep) and waits out flood waits. Reduce scanned channels per run. |
| TMDB rate limit warnings | The client paces calls (`TMDB_RATE_LIMIT_DELAY`, default 0.25s) and honors `Retry-After`. Increase the delay if you sync very large catalogs. |
| 429/401 from TMDB on `/api/v1/health` (`tmdb: disconnected`) | Invalid/expired TMDB key or token; verify at themoviedb.org → Settings → API. |
| Migration errors at boot | `startup.sh` runs `alembic upgrade head` before uvicorn; check deploy logs. To reset locally: `docker compose down -v` then `up --build`. |
| API returns `401 Invalid or missing X-API-Key` | `API_SECRET_KEY` is set — send the header on every request (health is exempt). |
| Search finds nothing for Tamil titles | Search uses config `simple` (language-agnostic). Ensure `title_tamil` was populated (TMDB `ta-IN` translation) — weekly sync/backfill updates it. |

---

### Repo layout

```
tamil-catalog/
├── app/                    # FastAPI app (config, models, schemas, api, services, utils)
├── migrations/             # Alembic env + 001_initial_schema (tables, indexes, FTS trigger)
├── scripts/                # generate_session.py, initial_scan.py, sync_tmdb.py
├── Dockerfile              # python:3.11-slim, non-root, startup.sh entrypoint
├── docker-compose.yml      # local dev: api + postgres:15-alpine
├── railway.toml            # Railway: Dockerfile builder, /api/v1/health healthcheck
├── startup.sh              # alembic upgrade head → uvicorn on $PORT
├── alembic.ini / requirements.txt / .env.example / .gitignore
└── README.md
```
