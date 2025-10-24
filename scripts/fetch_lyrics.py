#!/usr/bin/env python3
# fetch_lyrics.py
# Minimal, robust lyric fetcher with provider fallbacks and caching.

"""
Usage:
  export MUSIXMATCH_API_KEY="..."    # optional, best legal coverage
  export GENIUS_ACCESS_TOKEN="..."   # optional
  python3 fetch_lyrics.py "Artist Name" "Song Title"

Behavior:
  - Try Musixmatch (licensed) if API key present.
  - Else try Genius via lyricsgenius if token present.
  - Else try lyrics.ovh public endpoint.
  - Cache each successful fetch in ./lyrics_cache/{Artist} - {Title}.txt
  - Returns JSON with keys: ok(bool), provider, source_url, lyrics (str or None), error (str or None)
"""

from __future__ import annotations
import os
import sys
import time
import json
import logging
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
import requests
import backoff

# Prefer these libs; optional import handled below.
try:
    import lyricsgenius
except Exception:
    lyricsgenius = None

# Configuration
CACHE_DIR = Path("./lyrics_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
USER_AGENT = "karaoke-fetcher/1.0 (+https://example.invalid)"
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _cache_path(artist: str, title: str) -> Path:
    safe = (artist.strip() + " - " + title.strip())
    hashed = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:10]
    fname = f"{safe} [{hashed}].txt"
    return CACHE_DIR / fname


def _save_cache(artist: str, title: str, provider: str, source_url: Optional[str], text: str) -> None:
    p = _cache_path(artist, title)
    meta = {"provider": provider, "source_url": source_url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    content = json.dumps(meta, ensure_ascii=False) + "\n\n" + text
    p.write_text(content, encoding="utf-8")
    logging.info("Saved cache: %s", p)


def _load_cache(artist: str, title: str) -> Optional[Dict[str, Any]]:
    p = _cache_path(artist, title)
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8")
    try:
        meta_raw, lyrics = raw.split("\n\n", 1)
        meta = json.loads(meta_raw)
        return {"meta": meta, "lyrics": lyrics}
    except Exception:
        return None


# Generic requests wrapper with exponential backoff
@backoff.on_exception(backoff.expo, (requests.RequestException,), max_tries=4)
def _get(url: str, params=None, headers=None) -> requests.Response:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    resp = requests.get(url, params=params, headers=hdrs, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def try_musixmatch(artist: str, title: str, api_key: str) -> Dict[str, Any]:
    """
    Uses Musixmatch track.search -> track.lyrics.get
    Requires MUSIXMATCH_API_KEY with proper permissions.
    """
    base = "https://api.musixmatch.com/ws/1.1"
    # 1) search for track
    try:
        logging.info("Trying Musixmatch search for %s - %s", artist, title)
        s = _get(f"{base}/track.search", params={
            "q_track": title, "q_artist": artist, "f_has_lyrics": 1, "apikey": api_key, "page_size": 3
        })
        j = s.json()
        list_tracks = j.get("message", {}).get("body", {}).get("track_list", [])
        if not list_tracks:
            return {"ok": False, "error": "No track found on Musixmatch"}
        # pick best match (first)
        track = list_tracks[0]["track"]
        track_id = track["track_id"]
        # 2) get lyrics
        l = _get(f"{base}/track.lyrics.get", params={"track_id": track_id, "apikey": api_key})
        lj = l.json()
        lyrics_body = lj.get("message", {}).get("body", {}).get("lyrics", {})
        lyrics = lyrics_body.get("lyrics_body")
        if not lyrics:
            return {"ok": False, "error": "No lyrics field returned by Musixmatch"}
        # Musixmatch appends license notices; strip trailing disclaimer lines if present
        # Common pattern: "******* This Lyrics is NOT for Commercial use *******"
        # We'll keep first part until '...*******' if present
        cut_idx = lyrics.find("*******")
        if cut_idx != -1:
            lyrics = lyrics[:cut_idx].strip()
        source_url = track.get("track_share_url") or None
        _save_cache(artist, title, "musixmatch", source_url, lyrics)
        return {"ok": True, "provider": "musixmatch", "source_url": source_url, "lyrics": lyrics}
    except requests.HTTPError as e:
        return {"ok": False, "error": f"HTTP error from Musixmatch: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"Musixmatch error: {e}"}


def try_genius(artist: str, title: str, access_token: str) -> Dict[str, Any]:
    """
    Uses lyricsgenius (wrapper around Genius). lyricsgenius will search and attempt to extract
    lyrics by fetching the song page and scraping the lyrics block.
    """
    if lyricsgenius is None:
        return {"ok": False, "error": "lyricsgenius library not installed"}
    try:
        logging.info("Trying Genius for %s - %s", artist, title)
        g = lyricsgenius.Genius(access_token, timeout=REQUEST_TIMEOUT, skip_non_songs=True, excluded_terms=["(Remix)", "(Live)"])
        # reduce rate pressure
        g.headers["User-Agent"] = USER_AGENT
        song = g.search_song(title, artist)
        if not song:
            return {"ok": False, "error": "Genius did not return song"}
        lyrics = song.lyrics
        source_url = song.url if hasattr(song, "url") else None
        if not lyrics:
            return {"ok": False, "error": "No lyrics returned from Genius (page may be blocked)"}
        _save_cache(artist, title, "genius", source_url, lyrics)
        return {"ok": True, "provider": "genius", "source_url": source_url, "lyrics": lyrics}
    e
