#!/usr/bin/env python3
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────
import argparse
import json
import os
import subprocess
import requests
from dotenv import load_dotenv

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS,
)

TXT_DIR  = PATHS["txt"]
MP3_DIR  = PATHS["mp3"]
META_DIR = PATHS["meta"]

def load_mm_env() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not mm_api_key:
        log("ENV", "Missing Musixmatch API key; continuing without lyrics.", YELLOW)
        return ""
    return mm_api_key

def _safe_dict(obj):
    return obj if isinstance(obj, dict) else None

def musixmatch_search_track(artist: str, title: str, api_key: str) -> dict:
    url = "https://api.musixmatch.com/ws/1.1/track.search"
    params = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "page_size": 1,
        "q_artist": artist,
        "q_track": title,
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    message = _safe_dict(data.get("message"))
    if not message:
        return {}

    body = _safe_dict(message.get("body"))
    if not body:
        return {}

    track_list = body.get("track_list")
    if not isinstance(track_list, list) or not track_list:
        return {}

    entry = track_list[0]
    track = entry.get("track") if isinstance(entry, dict) else None
    if not isinstance(track, dict):
        return {}

    track_id = track.get("track_id")
    if not track_id:
        return {}

    return {
        "track_id": track_id,
        "artist": track.get("artist_name", artist),
        "title":  track.get("track_name", title),
    }

def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return ""

    message = _safe_dict(data.get("message"))
    if not message:
        return ""

    body = _safe_dict(message.get("body"))
    if not body:
        return ""

    lyrics_block = body.get("lyrics")
    if not isinstance(lyrics_block, dict):
        return ""

    lyrics = lyrics_block.get("lyrics_body", "")
    if not isinstance(lyrics, str) or not lyrics.strip():
        return ""

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics:
        lyrics = lyrics.split(footer)[0].strip()

    return lyrics.strip()

def youtube_download_mp3(artist: str, title: str, slug: str) -> None:
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    query = f"{artist} {title}".strip()
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "-o", out_template,
        f"ytsearch1:{query}",
    ]
    subprocess.run(cmd, check=True)

def parse_args():
    p = argparse.ArgumentParser(description="Step1: TXT + MP3 generation")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--slug", required=True)
    return p.parse_args()

def main():
    args = parse_args()
    artist = args.artist.strip()
    title  = args.title.strip()
    slug   = args.slug.strip()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    mm_key = load_mm_env()
    lyrics_text = ""
    lyrics_source = "none"

    if mm_key:
        track = musixmatch_search_track(artist, title, mm_key)
        if track:
            lyrics = musixmatch_fetch_lyrics(track["track_id"], mm_key)
            if lyrics:
                lyrics_text = lyrics
                lyrics_source = "musixmatch"
            else:
                log("LYRICS", "Lyrics unavailable; continuing without lyrics.", YELLOW)
        else:
            log("LYRICS", "Track not found; continuing without lyrics.", YELLOW)

    txt_path = TXT_DIR / f"{slug}.txt"
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote {txt_path}", GREEN)

    youtube_download_mp3(artist, title, slug)

    meta = {
        "slug": slug,
        "artist": artist,
        "title": title,
        "lyrics_source": lyrics_source,
    }
    meta_path = META_DIR / f"{slug}.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log("META", f"Wrote {meta_path}", GREEN)

if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
