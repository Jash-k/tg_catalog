import asyncio
import httpx
from rapidfuzz.fuzz import ratio, token_set_ratio, WRatio
from .config import settings

class TMDB:
    def __init__(self): self.index = 0
    async def get(self, path, params=None):
        params = dict(params or {}); params['api_key'] = settings.keys[self.index % len(settings.keys)]; params.setdefault('language', settings.tmdb_language)
        for attempt in range(len(settings.keys)):
            try:
                async with httpx.AsyncClient(base_url='https://api.themoviedb.org/3', timeout=25) as c:
                    r = await c.get(path, params=params)
                if r.status_code in (401, 429, 500, 502, 503):
                    self.index += 1; params['api_key'] = settings.keys[self.index % len(settings.keys)]; continue
                r.raise_for_status(); return r.json()
            except (httpx.HTTPError, asyncio.TimeoutError):
                self.index += 1; params['api_key'] = settings.keys[self.index % len(settings.keys)]
        return {}

    async def match(self, title, year, media_type):
        endpoint = '/search/tv' if media_type == 'series' else '/search/movie'
        year_key = 'first_air_date_year' if media_type == 'series' else 'year'
        data = await self.get(endpoint, {'query': title, year_key: year} if year else {'query': title})
        results = data.get('results', [])
        best, score = None, 0
        for x in results[:20]:
            candidate = x.get('name' if media_type == 'series' else 'title', '')
            a, b = title.casefold(), candidate.casefold()
            s = max(ratio(a, b), token_set_ratio(a, b), WRatio(a, b)) / 100
            if year:
                date = x.get('first_air_date' if media_type == 'series' else 'release_date', '')
                if date[:4] == str(year): s += .18
                elif date and abs(int(date[:4]) - year) > 1: s -= .12
            if s > score: best, score = x, s
        if not best or score < settings.min_match_confidence: return None, score
        return await self.details(best['id'], media_type), score

    async def details(self, tmdb_id, media_type):
        d = await self.get(f'/{"tv" if media_type == "series" else "movie"}/{tmdb_id}', {'append_to_response':'credits,translations,external_ids'})
        credits = d.get('credits', {})
        cast = [{'name': x.get('name'), 'character': x.get('character')} for x in credits.get('cast', [])[:10]]
        director = next((x.get('name') for x in credits.get('crew', []) if x.get('job') == 'Director'), None)
        tamil = next((x.get('data', {}).get('name') or x.get('data', {}).get('title') for x in d.get('translations', {}).get('translations', []) if x.get('iso_639_1') == 'ta'), None)
        title = d.get('name' if media_type == 'series' else 'title')
        return {'tmdb_id': d['id'], 'imdb_id': d.get('external_ids', {}).get('imdb_id') or d.get('imdb_id'), 'media_type': media_type, 'title': title, 'english_title': title,
                'tamil_title': tamil, 'overview': d.get('overview',''),
                'poster': ('https://image.tmdb.org/t/p/w500' + d['poster_path']) if d.get('poster_path') else None,
                'backdrop': ('https://image.tmdb.org/t/p/w1280' + d['backdrop_path']) if d.get('backdrop_path') else None,
                'genres': [x['name'] for x in d.get('genres', [])], 'cast': cast, 'director': director,
                'rating': d.get('vote_average'), 'runtime': d.get('runtime') or (d.get('episode_run_time') or [None])[0],
                'release_date': d.get('first_air_date' if media_type == 'series' else 'release_date'),
                'year': int((d.get('first_air_date' if media_type == 'series' else 'release_date') or '0')[:4]) or None,
                'original_language': d.get('original_language')}

    async def season_episodes(self, tmdb_id, imdb_id, season_number):
        d = await self.get(f'/tv/{tmdb_id}/season/{season_number}')
        videos = []
        for ep in d.get('episodes', []):
            ep_id = f'{imdb_id}:{season_number}:{ep.get("episode_number")}' if imdb_id else f'tmdb:series:{tmdb_id}:{season_number}:{ep.get("episode_number")}'
            videos.append({'id': ep_id, 'title': ep.get('name') or f'Episode {ep.get("episode_number")}',
                           'season': season_number, 'episode': ep.get('episode_number'),
                           'overview': ep.get('overview') or '', 'released': ep.get('air_date'),
                           'thumbnail': ('https://image.tmdb.org/t/p/w500' + ep['still_path']) if ep.get('still_path') else None,
                           'runtime': ep.get('runtime')})
        return videos
