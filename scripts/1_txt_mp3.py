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

# ─────────────────────────────────────────────
# ENV (MUSIXMATCH ONLY)
# ─────────────────────────────────────────────
def load_mm_env() -> str:
    """
    Load Musixmatch API key from .env or environment.
    Required: MUSIXMATCH_API_KEY or MM_API.
    """
    env_path = ROOT / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", CYAN)
        load_dotenv(env_path)
    else:
        log("ENV", ".env not found, relying on process environment", YELLOW)

    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not mm_api_key:
        log("ENV", "MUSIXMATCH_API_KEY (or MM_API) is not set.", RED)
        raise SystemExit("Missing Musixmatch API key.")
    return mm_api_key

# ─────────────────────────────────────────────
# MUSIXMATCH: SEARCH + LYRICS
# ─────────────────────────────────────────────
def musixmatch_search_track(artist: str, title: str, api_key: str) -> dict:
    """
    Exact Musixmatch track search using provided artist/title.
    Zero inference, zero guessing. Fail if not found.
    """
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
        log("MM", f"track.search failed: {e}", RED)
        raise SystemExit("Musixmatch track.search failed.")

    body = data.get("message", {}).get("body", {})
    track_list = body.get("track_list", [])
    if not track_list:
        log("MM", "No tracks found for given artist/title.", YELLOW)
        raise SystemExit("No matching track found on Musixmatch.")

    track = track_list[0].get("track", {})
    if not track.get("track_id"):
        log("MM", "Result missing track_id.", YELLOW)
        raise SystemExit("Musixmatch result missing track_id.")

    mm_artist = track.get("artist_name") or artist
    mm_title  = track.get("track_name") or title
    tid       = track.get("track_id")

    log("MM", f'Chosen track: "{mm_artist} - {mm_title}" (id={tid})', GREEN)
    return {
        "track_id": tid,
        "artist": mm_artist,
        "title": mm_title,
    }


def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    """
    Fetch lyrics for the given track_id.
    Fail if missing or empty.
    """
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}

    log("MM", f"Fetching lyrics for track_id={track_id}", CYAN)
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("MM", f"track.lyrics.get failed: {e}", RED)
        raise SystemExit("Musixmatch lyrics fetch failed.")

    body = data.get("message", {}).get("body", {})
    lyrics_obj = body.get("lyrics", {})
    lyrics_text = lyrics_obj.get("lyrics_body")

    if not lyrics_text or not lyrics_text.strip():
        log("MM", "Lyrics missing/empty.", YELLOW)
        raise SystemExit("Lyrics not found on Musixmatch.")

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics_text:
        lyrics_text = lyrics_text.split(footer, 1)[0].strip()

    log("MM", "Lyrics fetched.", GREEN)
    return lyrics_text

# ─────────────────────────────────────────────
# YT-DLP AUDIO DOWNLOAD
# ─────────────────────────────────────────────
def youtube_download_mp3(artist: str, title: str, slug: str) -> None:
    """
    Download audio using a simple, explicit search:
    ytsearch1:"{artist} {title}"
    """
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    query = f"{artist} {title}".strip()
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")

    log("YT", f'Downloading audio for "{query}"', CYAN)
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "-o", out_template,
        f"ytsearch1:{query}",
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log("YT", f"yt-dlp failed (exit {e.returncode}).", RED)
        raise SystemExit("yt-dlp audio download failed.")

# ─────────────────────────────────────────────
# ARG PARSING
# ─────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    """
    Non-interactive interface for Step1.

    0_master.py is responsible for:
      - Prompting user for Artist and Title.
      - Deriving the canonical slug from Title.
      - Deciding whether to overwrite existing artifacts.
    This script simply executes the txt/mp3 generation for the given inputs.
    """
    parser = argparse.ArgumentParser(
        description="Step1: Generate TXT + MP3 for a song (non-interactive)."
    )
    parser.add_argument("--artist", required=True, help="Artist name")
    parser.add_argument("--title", required=True, help="Song title")
    parser.add_argument("--slug", required=True, help="Canonical slug (from title)")
    return parser.parse_args()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log("MODE", "txt + mp3 generation", CYAN)

    args = parse_args()
    artist = args.artist.strip()
    title = args.title.strip()
    slug = args.slug.strip()

    if not artist or not title or not slug:
        log("INPUT", "Artist, Title, and Slug are required.", RED)
        raise SystemExit(1)

    # Load API key
    mm_api_key = load_mm_env()

    # Ensure directories exist
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # Musixmatch metadata
    track_info = musixmatch_search_track(artist, title, mm_api_key)
    mm_artist = track_info["artist"]
    mm_title  = track_info["title"]
    track_id  = track_info["track_id"]

    # Lyrics
    lyrics_text = musixmatch_fetch_lyrics(track_id, mm_api_key)

    # Slug is provided by 0_master (from Title); do NOT change it here.
    log("SLUG", f'Slug: "{slug}"', GREEN)

    # Paths
    txt_path  = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path  = MP3_DIR / f"{slug}.mp3"

    # Download YouTube audio
    youtube_download_mp3(mm_artist, mm_title, slug)

    # Write txt
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote: {txt_path}", GREEN)

    # Write meta.json
    meta = {
        "slug": slug,
        "artist": mm_artist,
        "title": mm_title,
        "musixmatch_track_id": track_id,
        "lyrics_source": "musixmatch",
        "audio_source": "yt-dlp",
        "youtube_query": f"{mm_artist} {mm_title}",
        "input_artist": artist,
        "input_title": title,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("META", f"Wrote: {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"Audio at: {mp3_path}", GREEN)
    else:
        log("MP3", "MP3 missing after download?", YELLOW)


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
