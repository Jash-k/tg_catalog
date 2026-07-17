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

def catalog_for(p, details, channel):
    cat = channel.get('category','other').lower()
    original_lang = (details.get('original_language') or '').lower()
    if p.anime: return 'anime'
    if p.media_type == 'series':
        return 'tamil_series' if cat in SOUTH else 'other_series'
    if p.dubbed: return 'dubbed_movies'
    if cat == 'tamil' and channel.get('original', True) and original_lang in ('ta',''):
        return 'tamil_movies'
    if cat not in SOUTH: return 'other_movies'
    # South Indian non-Tamil movies have no dedicated catalog; retain them in Other Movies
    # so no matched content is silently lost.
    return 'other_movies'

class Scanner:
    def __init__(self): self.tmdb = TMDB(); self.running = False
    async def scan(self):
        if self.running: return
        self.running = True
        try:
            client = TelegramClient(StringSession(settings.telegram_session_string), settings.telegram_api_id, settings.telegram_api_hash)
            await client.start()
            async with Session() as db:
                for channel in settings.channels:
                    try:
                        entity = await client.get_entity(channel['username'])
                        kwargs = {'limit': settings.max_messages_per_channel or None}
                        async for message in client.iter_messages(entity, **kwargs):
                            if not message or not (getattr(message, 'file', None) or getattr(message, 'message', None)): continue
                            raw = media_name(message)
                            if not raw: continue
                            parsed = parse_filename(raw, channel)
                            if len(parsed.title) < 2: continue
                            details, confidence = await self.tmdb.match(parsed.title, parsed.year, parsed.media_type)
                            if not details:
                                db.add(Unmatched(raw_name=raw[:1000], cleaned_title=parsed.title[:500], year=parsed.year, media_type=parsed.media_type, reason=f'no confident TMDB match ({confidence:.2f})'))
                                continue
                            catalog = catalog_for(parsed, details, channel)
                            result = await db.execute(select(Content).where(Content.tmdb_id == details['tmdb_id'], Content.media_type == parsed.media_type))
                            existing = result.scalar_one_or_none()
                            if existing:
                                if parsed.media_type == 'series': existing.seasons = sorted(set((existing.seasons or []) + parsed.seasons))
                                existing.catalog = catalog
                                existing.sort_priority = 0 if catalog == 'tamil_series' and channel.get('category') == 'tamil' else existing.sort_priority
                                existing.updated_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                            else:
                                details.pop('original_language', None)
                                details['catalog'] = catalog; details['sort_priority'] = 0 if catalog == 'tamil_series' and channel.get('category') == 'tamil' else 1; details['seasons'] = parsed.seasons
                                db.add(Content(**details))
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
