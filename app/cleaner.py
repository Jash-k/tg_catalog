import re
from dataclasses import dataclass

@dataclass
class ParsedName:
    raw: str
    title: str
    year: int | None
    media_type: str
    seasons: list[int]
    anime: bool
    dubbed: bool

WATERMARKS = [r'@\w+', r'www\.[\w.-]+', r'join\s+us\s+on\s+telegram', r't\.me[/\\][\w_]+']
QUALITY = r'(?i)\b(?:480p|576p|720p|1080p|1440p|2160p|4k|8k|web[- .]?dl|web rip|webrip|bluray|hq|blu[- .]?ray|brrip|hdrip|dvdrip|hdtv|x264|x265|h264|h265|hevc|avc|av1|10bit|12bit|atmos|dolby|truehd|remux|proper|repack|extended|uncut|remastered|multi(?:ple)?\s*audio|esub|msub|sample|zip|rar|7z|mkv|mp4|avi|mov|webm)\b'
SIZE = r'(?i)\b\d+(?:[. ]\d+)?\s*(?:gb|mb|tb)\b'


# Apply substitutions only to alphabetic runs, never to a four-digit year.
def fix_leet(s: str) -> str:
    def repl(m):
        w = m.group(0)
        if len(w) < 3 or w.isdigit() or re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)', w): return w
        return w.translate(str.maketrans({'0':'o','1':'i','3':'e','4':'a','5':'s','7':'t','8':'b'}))
    return re.sub(r'(?<!\d)[A-Za-z0-9]{3,}(?!\d)', repl, s)

def parse_filename(raw: str, channel: dict) -> ParsedName:
    # Prefer structured captions such as: Title : X, Year : 2024, Audio : Tamil + Multi.
    explicit_title = re.search(r'(?is)(?:🎬\s*)?(?:title|name)\s*:\s*(.*?)(?=\s*(?:🗓|📅|year\s*:|audio\s*:|quality\s*:|file\s+credit\s*:|$))', raw)
    explicit_year = re.search(r'(?i)\byear\s*:\s*((?:19|20)\d{2})', raw)
    source_text = explicit_title.group(1).strip() if explicit_title else raw
    # Remove watermarks while punctuation is still intact, then normalize separators.
    text = re.sub('|'.join(WATERMARKS), ' ', source_text)
    text = text.replace('_', ' ').replace('.', ' ').replace('-', ' - ')
    text = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', text)
    anime = bool(channel.get('category') == 'anime' or re.search(r'(?i)\b(?:anime|animation|animated|cartoon)\b', raw))
    dubbed = bool(re.search(r'(?i)\b(?:tamil|tam)\s*(?:dub(?:bed)?|audio|track)\b', raw))
    # Multi-audio blocks such as Tamil + Multi or Telugu + Tamil count as Tamil audio.
    if re.search(r'(?i)\b(?:tamil|tam)\b', raw) and re.search(r'(?i)\b(?:multi|telugu|hindi|malayalam|kannada|english|japanese)\b', raw):
        dubbed = True
    seasons = sorted({int(x) for x in re.findall(r'(?i)\b(?:S(?:eason)?|Season)\s*[:._-]?\s*([0-9]{1,2})(?=\b|\s*E|\s*Episode)', raw)})
    seasons += [int(x) for x in re.findall(r'(?i)\bseason\s*[:._-]?\s*([0-9]{1,2})\b', raw)]
    seasons += [int(a) for a, b in re.findall(r'(?i)\b(?:S|Season)\s*([0-9]{1,2})\s*[-–]?\s*S([0-9]{1,2})\b', raw)]
    seasons += [int(b) for a, b in re.findall(r'(?i)\b(?:S|Season)\s*([0-9]{1,2})\s*[-–]?\s*S([0-9]{1,2})\b', raw)]
    seasons = sorted(set(seasons))
    media_type = 'series' if seasons or re.search(r'(?i)\b(?:S\d{1,2}(?:E\d{1,3})?|E\d{1,3}|EP?\.?\s*\d+|complete\s*series|season)', raw) else 'movie'
    year_match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', raw)
    year = int(explicit_year.group(1)) if explicit_year else (int(year_match.group(1)) if year_match else None)
    # Remove channel/release prefixes and archive parts before title text.
    title = text
    title = re.sub(r'(?i)^\s*(?:vflix|www|tamilblasters?|telegram|moviezwap|moviesda)\s*[-_:|]*', ' ', title)
    title = re.sub(SIZE, ' ', title)
    title = re.sub(r'(?i)^\s*(?:[-_:|\[\]{}().]+\s*)+', ' ', title)
    # Also handle captions/filenames that begin with Season/Sxx before the title.
    title = re.sub(r'(?i)^\s*(?:season\s*\d{1,2}|s\d{1,2}(?:e\d{1,3})?)\s*[-_:|]+\s*', ' ', title)
    # Remove technical suffix/prefix blocks, including audio-language blocks.
    title = re.sub(QUALITY, ' ', title)
    title = re.sub(r'(?i)\b(?:tamil|telugu|malayalam|kannada|hindi|english|japanese|multi)\s*(?:audio|dub(?:bed)?|track)\b', ' ', title)
    title = re.sub(r'\[[^\]]*(?:www|telegram|subs?|audio|codec|\+|ddp|dts|atmos|bluray|hdrip|x26[45]|gb|mb|kbps)[^\]]*\]', ' ', title, flags=re.I)
    title = re.sub(r'\([^)]*(?:www|telegram|subs?|audio|codec|\+|ddp|dts|atmos|bluray|hdrip|x26[45]|gb|mb|kbps)[^)]*\)', ' ', title, flags=re.I)
    # Remove common scene/release-group suffixes, e.g. (W4F-Mack), after tags are gone.
    title = re.sub(r'\s*\([^)]{1,30}\)\s*$', ' ', title)
    title = re.sub(r'(?i)\b(?:zip|rar|7z|part\s*\d+|\d{3})\b', ' ', title)
    title_year_match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', title)
    if title_year_match and not explicit_title: title = title[:title_year_match.start()]
    if media_type == 'series':
        # For episode uploads, everything after Sxx/Eyy is release metadata.
        title = re.split(r'(?i)\s+S(?:eason)?\s*\d{1,2}(?:\s*[-–]\s*S\d{1,2})?(?:\s*E\d{1,3})?', title, maxsplit=1)[0]
    title = re.sub(r'(?i)\bS(?:eason)?\s*\d{1,2}\s*(?:[-–]?\s*S\d{1,2})?(?:\s*E\d{1,3})?\b', ' ', title)
    title = re.sub(r'(?i)\bE\d{1,3}\b|\b(?:mkv|mp4|avi|mov|webm)\b', ' ', title)
    title = fix_leet(title)
    title = re.sub(r'[^\w\s&\'’]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -_.')
    # Never invent Season 1. Only seasons explicitly parsed from channel media
    # are exposed as available seasons.
    return ParsedName(raw, title, year, media_type, sorted(set(seasons)) if media_type == 'series' else [], anime, dubbed)
