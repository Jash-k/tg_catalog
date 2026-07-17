import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from sqlalchemy import select
from .config import settings
from .db import Session, Content, Unmatched
from .cleaner import parse_filename
from .tmdb import TMDB

SOUTH = {'tamil','telugu','malayalam','kannada'}

def media_name(message):
    names = []
    if getattr(message, 'file', None) and getattr(message.file, 'name', None): names.append(message.file.name)
    if getattr(message, 'message', None): names.append(message.message)
    return max(names, key=len, default='')

def catalog_for(p, details, channel=None):
    # Classification is metadata-driven, so mixed channels do not need manual labels.
    original_lang = (details.get('original_language') or '').lower()
    genres = {x.lower() for x in (details.get('genres') or [])}
    is_anime = p.anime or (original_lang == 'ja' and 'animation' in genres)
    if is_anime: return 'anime'
    if p.media_type == 'series':
        return 'tamil_series' if original_lang in SOUTH_LANGUAGES else 'other_series'
    if p.dubbed: return 'dubbed_movies'
    if original_lang == 'ta': return 'tamil_movies'
    return 'other_movies'

SOUTH_LANGUAGES = {'ta','te','ml','kn'}

class Scanner:
    def __init__(self): self.tmdb = TMDB(); self.running = False
    async def scan(self):
        if self.running: return
        self.running = True
        try:
            client = TelegramClient(StringSession(settings.telegram_session_string), settings.telegram_api_id, settings.telegram_api_hash)
            await client.start()
            async with Session() as db:
                # One TMDB lookup per normalized title during a scan. Episode files
                # reuse the series result and never create episode metadata rows.
                match_cache = {}
                for channel in settings.channels:
                    try:
                        channel_ref = channel.get('id') if 'id' in channel else channel.get('username')
                        if channel_ref is None:
                            raise ValueError('channel requires id or username')
                        # Telegram supergroup/channel IDs are commonly written as -100123...
                        # Accept either a JSON number or a string in Railway variables.
                        if isinstance(channel_ref, str) and channel_ref.strip().lstrip('-').isdigit():
                            channel_ref = int(channel_ref.strip())
                        entity = await client.get_entity(channel_ref)
                        kwargs = {'limit': settings.max_messages_per_channel or None}
                        async for message in client.iter_messages(entity, **kwargs):
                            if not message or not (getattr(message, 'file', None) or getattr(message, 'message', None)): continue
                            raw = media_name(message)
                            if not raw: continue
                            parsed = parse_filename(raw, channel)
                            if len(parsed.title) < 2: continue
                            match_key = (parsed.title.casefold(), parsed.year, parsed.media_type)
                            if match_key in match_cache:
                                details, confidence = match_cache[match_key]
                            else:
                                details, confidence = await self.tmdb.match(parsed.title, parsed.year, parsed.media_type)
                                match_cache[match_key] = (details, confidence)
                            if not details:
                                db.add(Unmatched(raw_name=raw[:1000], cleaned_title=parsed.title[:500], year=parsed.year, media_type=parsed.media_type, reason=f'no confident TMDB match ({confidence:.2f})'))
                                continue
                            catalog = catalog_for(parsed, details, channel)
                            result = await db.execute(select(Content).where(Content.tmdb_id == details['tmdb_id'], Content.media_type == parsed.media_type))
                            existing = result.scalar_one_or_none()
                            if existing:
                                if parsed.media_type == 'series': existing.seasons = sorted(set((existing.seasons or []) + parsed.seasons))
                                existing.catalog = catalog
                                existing.sort_priority = 0 if catalog == 'tamil_series' and details.get('original_language') == 'ta' else existing.sort_priority
                                existing.updated_at = __import__('datetime').datetime.utcnow()
                            else:
                                is_tamil_series = catalog == 'tamil_series' and details.get('original_language') == 'ta'
                                record = {k: v for k, v in details.items() if k != 'original_language'}
                                record['catalog'] = catalog; record['sort_priority'] = 0 if is_tamil_series else 1; record['seasons'] = parsed.seasons
                                db.add(Content(**record))
                            await db.commit()
                    except Exception as e:
                        print(f'channel scan failed for {channel}: {e}')
            await client.disconnect()
        finally: self.running = False

    async def refresh_metadata(self):
        async with Session() as db:
            rows = (await db.execute(select(Content))).scalars().all()
            for row in rows:
                try:
                    d = await self.tmdb.details(row.tmdb_id, row.media_type)
                    seasons = row.seasons
                    catalog = row.catalog
                    for k,v in d.items():
                        if k not in ('tmdb_id','media_type'): setattr(row,k,v)
                    row.seasons = seasons; row.catalog = catalog
                except Exception as e: print(f'metadata refresh failed for {row.tmdb_id}: {e}')
            await db.commit()

async def scheduler(scanner):
    first = True
    while True:
        try:
            if first:
                async with Session() as db: empty = not (await db.execute(select(Content.id).limit(1))).scalar_one_or_none()
                if empty: await scanner.scan()
                first = False
            else: await scanner.scan()
        except Exception as e: print(f'scheduled scan failed: {e}')
        await asyncio.sleep(settings.scan_interval_hours * 3600)

async def metadata_scheduler(scanner):
    while True:
        await asyncio.sleep(settings.tmdb_refresh_interval_hours * 3600)
        try: await scanner.refresh_metadata()
        except Exception as e: print(f'metadata refresh failed: {e}')
