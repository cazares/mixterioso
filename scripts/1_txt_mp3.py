#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

# ─────────────────────────────────────────────
def load_mm_env() -> str:
    env_path = PATHS["base"] / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", CYAN)
        load_dotenv(env_path)
    else:
        log("ENV", ".env not found, relying on environment", YELLOW)

    key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not key:
        log("ENV", "Missing MUSIXMATCH_API_KEY (or MM_API)", RED)
        raise SystemExit("Musixmatch API key missing.")
    return key

# ─────────────────────────────────────────────
def musixmatch_search_track(artist: str, title: str, api_key: str) -> dict:
    params = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "s_track_rating": "desc",
        "page_size": 1,
        "q_artist": artist,
        "q_track": title,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search"
    log("MM", f'Searching: "{artist} - {title}"', CYAN)

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("MM", f"track.search failed: {e}", RED)
        raise SystemExit("Musixmatch track.search error.")

    track_list = data.get("message", {}).get("body", {}).get("track_list", [])
    if not track_list:
        raise SystemExit("No Musixmatch results found.")

    track = track_list[0].get("track", {})
    tid = track.get("track_id")
    mm_art = track.get("artist_name") or artist
    mm_title = track.get("track_name") or title

    if not tid:
        raise SystemExit("Musixmatch track result missing track_id.")

    log("MM", f'Chosen: "{mm_art} - {mm_title}" (track_id={tid})', GREEN)
    return {"track_id": tid, "artist": mm_art, "title": mm_title}

# ─────────────────────────────────────────────
def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}
    log("MM", f"Fetching lyrics for track_id={track_id}", CYAN)

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("MM", f"lyrics.get failed: {e}", RED)
        raise SystemExit("Musixmatch lyrics error.")

    lyrics = data.get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body")
    if not lyrics:
        raise SystemExit("Empty lyrics.")

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics:
        lyrics = lyrics.split(footer, 1)[0].strip()

    return lyrics

# ─────────────────────────────────────────────
def youtube_download_mp3(search_str: str, slug: str) -> None:
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")
    log("YT", f'Downloading audio for "{search_str}"', CYAN)
    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3",
        "-o", out_template,
        f"ytsearch1:{search_str}",
    ]
    subprocess.run(cmd, check=True)

# ─────────────────────────────────────────────
def prompt_artist_title(query: str) -> tuple[str,str]:
    print()
    print("We need artist and title for Musixmatch.")
    print(f'Base query: "{query}"')
    print()

    try:
        artist = input("Artist: ").strip()
    except EOFError:
        artist = ""
    try:
        title = input("Title: ").strip()
    except EOFError:
        title = ""

    if not artist and not title and " - " in query:
        left, right = query.split(" - ", 1)
        artist = left.strip()
        title  = right.strip()

    if not artist or not title:
        raise SystemExit("Artist and title required.")

    return artist, title

# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate txt+mp3 using Musixmatch + yt-dlp")
    p.add_argument("query", nargs="+", help="Search query")
    return p.parse_args(argv)

# ─────────────────────────────────────────────
def main(argv=None):
    # TODO: reintroduce ensure_pipeline_dirs() once it exists in mix_utils
    # ensure_pipeline_dirs()

    args = parse_args(argv or sys.argv[1:])
    query = " ".join(args.query).strip()

    log("MODE", f'txt+mp3 generation for "{query}"', CYAN)

    api = load_mm_env()

    artist, title = prompt_artist_title(query)
    track = musixmatch_search_track(artist, title, api)

    lyrics = musixmatch_fetch_lyrics(track["track_id"], api)

    slug = slugify(track["title"])
    log("SLUG", f'Slug: "{slug}"', GREEN)

    txt_path  = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path  = MP3_DIR / f"{slug}.mp3"

    search_str = f"{track['artist']} {track['title']}".strip()
    youtube_download_mp3(search_str, slug)

    txt_path.write_text(lyrics, encoding="utf-8")
    log("TXT", f"Wrote lyrics to {txt_path}", GREEN)

    meta = {
        "slug": slug,
        "query": query,
        "artist": track["artist"],
        "title": track["title"],
        "musixmatch_track_id": track["track_id"],
        "lyrics_source": "musixmatch",
        "audio_source": "yt-dlp",
        "youtube_search_query": search_str,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("META", f"Wrote meta JSON to {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"mp3 saved to {mp3_path}", GREEN)
    else:
        log("MP3", f"Expected mp3 at {mp3_path} but file missing", YELLOW)

if __name__ == "__main__":
    main()
# end of 1_txt_mp3.py
