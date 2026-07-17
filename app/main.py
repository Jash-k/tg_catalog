import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from .config import settings
from .db import init_db, Session, Content
from .scanner import Scanner, scheduler, metadata_scheduler, progress_logger

CATALOGS = [('tamil_movies','Tamil Movies'),('dubbed_movies','Dubbed Movies'),('tamil_series','Tamil Series'),('other_movies','Other Movies'),('other_series','Other Series'),('anime','Anime')]
LANGUAGES = [('ta','Tamil'),('ml','Malayalam'),('te','Telugu'),('kn','Kannada'),('hi','Hindi'),('bn','Bengali'),('mr','Marathi'),('gu','Gujarati'),('pa','Punjabi'),('en','English'),('ko','Korean'),('ja','Japanese'),('zh','Chinese'),('es','Spanish'),('fr','French'),('de','German'),('pt','Portuguese'),('ru','Russian'),('ar','Arabic'),('tr','Turkish'),('id','Indonesian'),('th','Thai')]
# Stremio manifest options must be an array of strings, not {title,value} objects.
# The backend accepts the language code from the selected option.
LANGUAGE_OPTIONS = [title for code, title in LANGUAGES]
LANGUAGE_CODES = {title.lower(): code for code, title in LANGUAGES}
scanner = Scanner()

def manifest():
    cats = []
    for cid, name in CATALOGS:
        cats.append({'id': cid, 'type':'series' if 'series' in cid else 'movie', 'name': name,
                     'extra':[{'name':'search','isRequired':False},{'name':'genre','isRequired':False},{'name':'language','isRequired':False,'options':LANGUAGE_OPTIONS},{'name':'skip','isRequired':False}]})
    return {'id':'com.telegram.tmdb.catalog','version':'1.0.0','name':'Telegram TMDB Catalog','description':'Metadata catalog scanned from configured Telegram channels. No streams are provided.',
            'logo':'https://www.themoviedb.org/assets/2/v4/logos/one-color-blue.svg','resources':['catalog','meta'],'types':['movie','series'],'catalogs':cats,
            'idPrefixes':['tmdb:']}

def item(row):
    name = row.tamil_title or row.title if row.catalog == 'tamil_series' else row.title
    # Preserve both names for discoverability without making the title noisy.
    if row.tamil_title and row.tamil_title != row.title: name = f'{row.tamil_title} ({row.title})'
    obj = {'id':f'tmdb:{row.media_type}:{row.tmdb_id}','type':row.media_type,'name':name,'description':row.overview or '',
           'poster':row.poster,'background':row.backdrop,'year':row.year,'imdbRating':row.rating,'genres':row.genres or [],
           'director':row.director,'cast':[x.get('name') for x in (row.cast or [])], 'releaseInfo':str(row.year or ''), 'language':row.original_language}
    if row.media_type == 'series': obj['videos'] = []
    return obj

@asynccontextmanager
async def lifespan(app):
    await init_db()
    task = asyncio.create_task(scheduler(scanner))
    refresh_task = asyncio.create_task(metadata_scheduler(scanner))
    progress_task = asyncio.create_task(progress_logger(scanner))
    yield
    task.cancel(); refresh_task.cancel(); progress_task.cancel()

app = FastAPI(title='Telegram TMDB Stremio Addon', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
@app.get('/manifest.json')
async def get_manifest(): return manifest()
@app.get('/catalog/{kind}/{catalog_id}.json')
async def catalog(kind: str, catalog_id: str, request: Request):
    return await catalog_impl(catalog_id, request)
@app.get('/catalog/{kind}/{catalog_id}/{extra:path}.json')
async def catalog_extra(kind: str, catalog_id: str, extra: str, request: Request):
    return await catalog_impl(catalog_id, request)
async def catalog_impl(catalog_id, request):
    if catalog_id not in {x[0] for x in CATALOGS}: return JSONResponse({'metas':[]})
    q = request.query_params.get('search','').strip()
    genre = request.query_params.get('genre','').strip().lower()
    language = request.query_params.get('language','').strip().lower()
    language = LANGUAGE_CODES.get(language, language)
    try: skip = max(0, int(request.query_params.get('skip','0')))
    except ValueError: skip = 0
    page = settings.page_size
    async with Session() as db:
        stmt = select(Content).where(Content.catalog == catalog_id)
        if q: stmt = stmt.where(Content.title.ilike(f'%{q}%'))
        if genre: stmt = stmt.where(Content.genres.contains([request.query_params.get('genre')]))
        if language: stmt = stmt.where(Content.original_language == language)
        # Tamil original series first; then newest metadata.
        stmt = stmt.order_by(Content.sort_priority.asc(), Content.year.desc().nullslast(), Content.title.asc()).offset(skip).limit(page)
        rows = (await db.execute(stmt)).scalars().all()
    return {'metas':[item(x) for x in rows]}

@app.get('/meta/{kind}/{meta_id}.json')
async def meta(kind: str, meta_id: str):
    try: tmdb_id = int(meta_id.split(':')[-1])
    except ValueError: return JSONResponse({'meta':None}, status_code=404)
    async with Session() as db:
        row = (await db.execute(select(Content).where(Content.tmdb_id == tmdb_id))).scalars().first()
    return {'meta': item(row) if row else None}

@app.get('/health')
async def health():
    return {
        'ok': True,
        'scan_running': scanner.running,
        'last_scan_started': scanner.last_scan_started,
        'last_scan_completed': scanner.last_scan_completed,
        'last_scan_stats': scanner.last_scan_stats,
        'current_scan_stats': scanner.current_scan_stats,
        'last_scan_error': scanner.last_scan_error,
    }
