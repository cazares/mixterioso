#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from scripts.mix_utils import (
    log, fatal, slugify, PATHS, ask_yes_no,
    CYAN, GREEN, YELLOW, RED
)

# Paths
BASE_DIR = PATHS["base"]
TXT_DIR  = PATHS["txt"]
MP3_DIR  = PATHS["mp3"]
META_DIR = PATHS["meta"]


# ENV
def load_mm_env() -> str:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", CYAN)
        load_dotenv(env_path)
    else:
        log("ENV", ".env not found, relying on process environment", YELLOW)

    key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not key:
        fatal("Missing Musixmatch API key (MUSIXMATCH_API_KEY or MM_API).", "ENV")
    return key


# MUSIXMATCH
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
    log("MM", f'Searching track: "{artist} - {title}"', CYAN)

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        fatal(f"Musixmatch track.search failed: {e}", "MM")

    body = data.get("message", {}).get("body", {})
    tracks = body.get("track_list", [])
    if not tracks:
        fatal("No tracks found.", "MM")

    track = tracks[0].get("track", {})
    tid = track.get("track_id")
    if not tid:
        fatal("First result missing track_id.", "MM")

    mm_artist = track.get("artist_name") or artist
    mm_title  = track.get("track_name") or title

    log("MM", f'Chosen track: "{mm_artist} - {mm_title}" (track_id={tid})', GREEN)
    return {"track_id": tid, "artist": mm_artist, "title": mm_title}


def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}

    log("MM", f"Fetching lyrics for track_id={track_id}", CYAN)

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        fatal(f"Musixmatch lyrics fetch failed: {e}", "MM")

    body = data.get("message", {}).get("body", {})
    lyrics_obj = body.get("lyrics", {})
    text = lyrics_obj.get("lyrics_body")

    if not text or not text.strip():
        fatal("Lyrics missing for this track.", "MM")

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in text:
        text = text.split(footer, 1)[0].strip()

    log("MM", "Lyrics fetched.", GREEN)
    return text


# YT-DLP
def youtube_download_mp3(search_str: str, slug: str):
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out = str(MP3_DIR / f"{slug}.%(ext)s")

    log("YT", f'Downloading audio: "{search_str}"', CYAN)
    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3",
        "-o", out,
        f"ytsearch1:{search_str}",
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        fatal(f"yt-dlp failed (exit {e.returncode}).", "YT")


# INTERACTIVE ARTIST/TITLE
def prompt_artist_title(query: str) -> tuple[str, str]:
    print()
    print("We need artist and title. Query:", query)
    print("If both empty and query looks like 'Artist - Title', will split.")

    try:
        a = input("Artist: ").strip()
    except EOFError:
        a = ""

    try:
        t = input("Title : ").strip()
    except EOFError:
        t = ""

    if not a and not t and " - " in query:
        left, right = query.split(" - ", 1)
        a = left.strip()
        t = right.strip()

    if not a or not t:
        fatal("Artist and title required.", "INPUT")

    return a, t


# ARGS
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate txt+mp3 via Musixmatch + yt-dlp.")
    p.add_argument("query", nargs="+", help="Search query")
    return p.parse_args(argv)


# MAIN
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    query = " ".join(args.query).strip()

    log("MODE", f'txt+mp3 generation for "{query}"', CYAN)

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    api_key = load_mm_env()

    artist, title = prompt_artist_title(query)
    track = musixmatch_search_track(artist, title, api_key)

    lyrics = musixmatch_fetch_lyrics(track["track_id"], api_key)

    slug = slugify(track["title"])
    log("SLUG", f'Slug: "{slug}"', GREEN)

    txt_path  = TXT_DIR  / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path  = MP3_DIR  / f"{slug}.mp3"

    search_str = f"{track['artist']} {track['title']}".strip()
    youtube_download_mp3(search_str, slug)

    txt_path.write_text(lyrics, encoding="utf-8")
    log("TXT", f"Wrote {txt_path}", GREEN)

    meta = {
        "slug": slug,
        "query": query,
        "artist": track["artist"],
        "title": track["title"],
        "track_id": track["track_id"],
        "lyrics_source": "musixmatch",
        "audio_source": "yt-dlp",
        "youtube_search_query": search_str,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log("META", f"Wrote {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"Audio at {mp3_path}", GREEN)
    else:
        log("MP3", f"Expected mp3 at {mp3_path} but not found.", RED)


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
