"""Parse `soundcache://` deep links fired by the website's "Get this sound" button.

The hits/leaderboard page links `soundcache://ingest?sound_id=<music_id>&title=…&artist=…`.
`sound_id` is the TikTok music id (the desktop reports it as such to the leaderboard),
so we synthesize a clean canonical `/music/<slug>-<id>` URL the ingest pipeline can fetch.
Kept Qt-free so it is unit-testable without a display.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class SoundCacheDeepLink:
    sound_id: str
    title: str
    artist: str
    music_url: str


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def parse_soundcache_url(url: str) -> SoundCacheDeepLink | None:
    """Return a validated deep link, or None if the URL isn't a usable ingest link."""
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme != "soundcache":
        return None
    # soundcache://ingest?… → netloc 'ingest'; soundcache:ingest?… → path 'ingest'
    action = (parsed.netloc or parsed.path.lstrip("/")).lower()
    if action != "ingest":
        return None
    query = parse_qs(parsed.query)
    sound_id = (query.get("sound_id", [""])[0] or "").strip()
    if not sound_id.isdigit():  # TikTok music ids are numeric; reject anything else
        return None
    title = (query.get("title", [""])[0] or "").strip()
    artist = (query.get("artist", [""])[0] or "").strip()
    slug = _slugify(title or artist) or f"sound-{sound_id[-6:]}"
    music_url = f"https://www.tiktok.com/music/{slug}-{sound_id}"
    return SoundCacheDeepLink(sound_id=sound_id, title=title, artist=artist, music_url=music_url)
