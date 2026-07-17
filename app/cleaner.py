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
QUALITY = r'(?i)\b(?:480p|576p|720p|1080p|1440p|2160p|4k|8k|web[- .]?dl|web rip|webrip|bluray|brrip|hdrip|dvdrip|hdtv|x264|x265|h264|h265|hevc|av1|10bit|ddp?\s*\d*\.?\d*|aac\s*\d*\.?\d*|proper|repack|extended|uncut|remastered|multi(?:ple)?\s*audio|esub|msub|sample)\b'

# Apply substitutions only to alphabetic runs, never to a four-digit year.
def fix_leet(s: str) -> str:
    def repl(m):
        w = m.group(0)
        if len(w) < 3: return w
        return w.translate(str.maketrans({'0':'o','1':'i','3':'e','4':'a','5':'s','7':'t','8':'b'}))
    return re.sub(r'(?<!\d)[A-Za-z0-9]{3,}(?!\d)', repl, s)

def parse_filename(raw: str, channel: dict) -> ParsedName:
    text = raw.replace('_', ' ').replace('.', ' ').replace('-', ' - ')
    anime = bool(channel.get('category') == 'anime' or re.search(r'(?i)\banime\b|\[\w+\]\s*[-–].*\b(?:1080p|720p)\b', raw))
    dubbed = bool(re.search(r'(?i)\b(?:tamil\s*(?:dub|audio)|tam\s*(?:dub|audio)|tamil dubbed|dual audio tamil|tamil track)\b', raw))
    seasons = sorted({int(x) for x in re.findall(r'(?i)\bS(?:eason)?\s*([0-9]{1,2})(?=\b|\s*E)', raw)})
    seasons += [int(a) for a, b in re.findall(r'(?i)\b(?:S|Season)\s*([0-9]{1,2})\s*[-–]?\s*S([0-9]{1,2})\b', raw)]
    seasons += [int(b) for a, b in re.findall(r'(?i)\b(?:S|Season)\s*([0-9]{1,2})\s*[-–]?\s*S([0-9]{1,2})\b', raw)]
    seasons = sorted(set(seasons))
    media_type = 'series' if seasons or re.search(r'(?i)\b(?:S\d{1,2}(?:E\d{1,3})?|E\d{1,3}|EP?\.?\s*\d+|complete\s*series|season)', raw) else 'movie'
    year_match = re.search(r'(?<!\d)((?:19|20)\d{2})(?!\d)', raw)
    year = int(year_match.group(1)) if year_match else None
    title = re.sub('|'.join(WATERMARKS), ' ', text)
    title = re.sub(QUALITY, ' ', title)
    title = re.sub(r'(?i)\b(?:tamil|telugu|malayalam|kannada|hindi|english|japanese)\s*(?:audio|dub(?:bed)?)\b', ' ', title)
    title = re.sub(r'\[[^\]]*\]|\([^)]*(?:www|telegram|subs?|audio|codec|\+|ddp|dts|w4f|mack)[^)]*\)', ' ', title, flags=re.I)
    # Remove common scene/release-group suffixes, e.g. (W4F-Mack), after tags are gone.
    title = re.sub(r'\s*\([^)]{1,30}\)\s*$', ' ', title)
    if year_match: title = title[:year_match.start()]
    title = re.sub(r'(?i)\bS(?:eason)?\s*\d{1,2}\s*(?:[-–]?\s*S\d{1,2})?(?:\s*E\d{1,3})?\b', ' ', title)
    title = re.sub(r'(?i)\bE\d{1,3}\b|\b(?:mkv|mp4|avi|mov|webm)\b', ' ', title)
    title = fix_leet(title)
    title = re.sub(r'[^\w\s&\'’]', ' ', title)
    title = re.sub(r'\s+', ' ', title).strip(' -_.')
    return ParsedName(raw, title, year, media_type, seasons or [1] if media_type == 'series' else [], anime, dubbed)
