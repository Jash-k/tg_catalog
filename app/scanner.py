import asyncio
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
from sqlalchemy import select
from .config import settings
from .db import Session, Content, Unmatched, ScanTracker
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
    is_anime = p.anime or 'animation' in genres or 'cartoon' in genres or (original_lang == 'ja' and 'animation' in genres)
    # Keep the Dubbed Movies catalog movie-only so Stremio never receives
    # series/anime/collection records under a movie catalog.
    if is_anime: return 'anime_series' if p.media_type == 'series' else 'anime_movies'
    if p.media_type == 'movie' and p.dubbed and original_lang != 'ta': return 'dubbed_movies'
    # Collections accept Tamil, Malayalam, English/Hollywood, and non-anime animation movies.
    if p.media_type == 'movie' and details.get('collection_id') and (original_lang in {'ta','ml','en'} or 'animation' in genres):
        return 'collections'
    if p.media_type == 'series':
        return 'tamil_series' if original_lang in SOUTH_LANGUAGES else 'other_series'
    if p.dubbed: return 'dubbed_movies'
    if original_lang == 'ta': return 'tamil_movies'
    return 'other_movies'

SOUTH_LANGUAGES = {'ta','te','ml','kn'}

class Scanner:
    def __init__(self):
        self.tmdb = TMDB()
        self.running = False
        self.last_scan_started = None
        self.last_scan_completed = None
        self.last_scan_stats = {}
        self.current_scan_stats = {}
        self.last_scan_error = None
    async def scan(self):
        if self.running:
            print('scan skipped: another scan is already running', flush=True)
            return
        self.running = True
        self.last_scan_started = datetime.utcnow().isoformat() + 'Z'
        self.last_scan_error = None
        stats = {'channels': 0, 'messages': 0, 'matched': 0, 'unmatched': 0, 'errors': 0}
        self.current_scan_stats = stats
        print(f'scan started: {len(settings.channels)} configured channel(s)', flush=True)
        try:
            client = TelegramClient(StringSession(settings.telegram_session_string), settings.telegram_api_id, settings.telegram_api_hash)
            await client.start()
            async with Session() as db:
                # One TMDB lookup per normalized title during a scan. Episode files
                # reuse the series result and never create episode metadata rows.
                match_cache = {}
                collection_cache = {}
                for channel in settings.channels:
                    stats['channels'] += 1
                    try:
                        channel_ref = channel.get('id') if 'id' in channel else channel.get('username')
                        if channel_ref is None:
                            raise ValueError('channel requires id or username')
                        # Telegram supergroup/channel IDs are commonly written as -100123...
                        # Accept either a JSON number or a string in Railway variables.
                        if isinstance(channel_ref, str) and channel_ref.strip().lstrip('-').isdigit():
                            channel_ref = int(channel_ref.strip())
                        entity = await client.get_entity(channel_ref)
                        # Use the stable Telegram entity ID, not a mutable username, as the checkpoint key.
                        channel_key = str(entity.id)
                        tracker = (await db.execute(select(ScanTracker).where(ScanTracker.channel_key == channel_key))).scalar_one_or_none()
                        if tracker is None:
                            tracker = ScanTracker(channel_key=channel_key, last_message_id=0, historical_scan_completed=False)
                            db.add(tracker)
                            await db.flush()
                        kwargs = {}
                        if settings.max_messages_per_channel:
                            kwargs['limit'] = settings.max_messages_per_channel
                        if tracker.last_message_id:
                            kwargs['min_id'] = tracker.last_message_id
                        last_processed_id = tracker.last_message_id
                        mode = 'incremental' if tracker.historical_scan_completed else 'historical'
                        print(f'channel {channel_key}: {mode} scan from message {tracker.last_message_id}', flush=True)
                        # Process channels in configured order and messages oldest-to-newest.
                        async for message in client.iter_messages(entity, reverse=True, **kwargs):
                            if getattr(message, 'id', None):
                                last_processed_id = max(last_processed_id, message.id)
                            stats['messages'] += 1
                            # Scan documents/media files only; ignore text posts, stickers,
                            # webp images, and other non-document attachments.
                            file = getattr(message, 'file', None)
                            if not message or not file or not getattr(message, 'document', None): continue
                            if getattr(file, 'mime_type', '') == 'image/webp' or getattr(file, 'ext', '').lower() in ('.webp', '.tgs', '.webm'): continue
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
                                stats['unmatched'] += 1
                                db.add(Unmatched(raw_name=raw[:1000], cleaned_title=parsed.title[:500], year=parsed.year, media_type=parsed.media_type, reason=f'no confident TMDB match ({confidence:.2f})', created_at=datetime.utcnow()))
                                continue
                            stats['matched'] += 1
                            catalog = catalog_for(parsed, details, channel)
                            if catalog == 'collections':
                                collection_id = details['collection_id']
                                if collection_id not in collection_cache:
                                    collection_cache[collection_id] = await self.tmdb.collection_parts(collection_id)
                                collection = collection_cache[collection_id]
                                parts = collection.get('parts', [])
                                ordered_ids = [part.get('id') for part in parts]
                                details['collection_order'] = (ordered_ids.index(details['tmdb_id']) + 1) if details['tmdb_id'] in ordered_ids else 9999
                                details['collection_popularity'] = sum(float(part.get('vote_average') or 0) for part in parts)
                            result = await db.execute(select(Content).where(Content.tmdb_id == details['tmdb_id'], Content.media_type == parsed.media_type))
                            existing = result.scalar_one_or_none()
                            if existing:
                                if parsed.media_type == 'series': existing.seasons = sorted(set((existing.seasons or []) + parsed.seasons))
                                existing.catalog = catalog
                                existing.collection_id = details.get('collection_id')
                                existing.collection_name = details.get('collection_name')
                                existing.collection_order = details.get('collection_order')
                                existing.collection_popularity = details.get('collection_popularity')
                                # Refresh LIFO position whenever the title is discovered again.
                                # This brings newly posted/reposted releases to the front.
                                existing.discovered_at = datetime.utcnow()
                                existing.imdb_id = details.get('imdb_id') or existing.imdb_id
                                existing.original_language = details.get('original_language') or existing.original_language
                                existing.sort_priority = 0 if catalog == 'tamil_series' and details.get('original_language') == 'ta' else existing.sort_priority
                                existing.updated_at = __import__('datetime').datetime.utcnow()
                            else:
                                is_tamil_series = catalog == 'tamil_series' and details.get('original_language') == 'ta'
                                record = dict(details)
                                record['catalog'] = catalog; record['sort_priority'] = 0 if is_tamil_series else 1; record['seasons'] = parsed.seasons; record['discovered_at'] = datetime.utcnow(); record['updated_at'] = datetime.utcnow()
                                db.add(Content(**record))
                            await db.commit()
                        # Commit the checkpoint only after this channel completed successfully.
                        tracker.last_message_id = last_processed_id
                        tracker.historical_scan_completed = True
                        tracker.last_scan_at = datetime.utcnow()
                        await db.commit()
                        print(f'channel {channel_key}: checkpoint saved at message {last_processed_id}', flush=True)
                    except Exception as e:
                        stats['errors'] += 1
                        # A failed PostgreSQL statement leaves the transaction aborted.
                        # Roll it back before moving to the next channel.
                        try:
                            await db.rollback()
                        except Exception as rollback_error:
                            print(f'channel rollback failed for {channel}: {rollback_error}', flush=True)
                        print(f'channel scan failed for {channel}: {e}', flush=True)
            await client.disconnect()
            self.last_scan_stats = stats
            self.last_scan_completed = datetime.utcnow().isoformat() + 'Z'
            print(f'scan completed: {stats}', flush=True)
        except Exception as e:
            self.last_scan_error = repr(e)
            print(f'scan failed: {e}', flush=True)
            raise
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
    print('scheduler started', flush=True)
    while True:
        try:
            print('scheduler tick: starting scan check', flush=True)
            if first:
                async with Session() as db:
                    empty = (await db.execute(select(Content.id).limit(1))).scalar_one_or_none() is None
                print('first scan mode: full historical scan' if empty else 'first scan mode: existing catalog; refreshing channel scan', flush=True)
                await scanner.scan()
                first = False
            else:
                await scanner.scan()
        except Exception as e: print(f'scheduled scan failed: {e}')
        await asyncio.sleep(settings.scan_interval_hours * 3600)

async def progress_logger(scanner):
    """Emit a Railway-friendly progress heartbeat every minute."""
    while True:
        await asyncio.sleep(60)
        if scanner.running:
            print(f'scan progress: started={scanner.last_scan_started} stats={scanner.current_scan_stats}', flush=True)
        else:
            print(f'scan idle: last_completed={scanner.last_scan_completed} stats={scanner.last_scan_stats}', flush=True)

async def metadata_scheduler(scanner):
    while True:
        await asyncio.sleep(settings.tmdb_refresh_interval_hours * 3600)
        try: await scanner.refresh_metadata()
        except Exception as e: print(f'metadata refresh failed: {e}')
