#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# COLORS + LOGGING
# ─────────────────────────────────────────────
RESET  = "\033[0m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR  = BASE_DIR / "txts"
MP3_DIR  = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"


# ─────────────────────────────────────────────
# SLUGIFY
# ─────────────────────────────────────────────
def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# ─────────────────────────────────────────────
# ENV (MUSIXMATCH ONLY)
# ─────────────────────────────────────────────
def load_mm_env() -> str:
    """
    Load Musixmatch API key from .env or environment.
    Required: MUSIXMATCH_API_KEY or MM_API.
    """
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", CYAN)
        load_dotenv(env_path)
    else:
        log("ENV", ".env not found, relying on process environment", YELLOW)

    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not mm_api_key:
        log("ENV", "MUSIXMATCH_API_KEY (or MM_API) is not set.", RED)
        raise SystemExit("Missing Musixmatch API key in environment.")
    return mm_api_key


# ─────────────────────────────────────────────
# MUSIXMATCH (METADATA + LYRICS)
# ─────────────────────────────────────────────
def musixmatch_search_track(artist: str, title: str, api_key: str) -> dict:
    """
    Find a single best track for (artist, title) using Musixmatch.
    Fails with SystemExit if nothing reasonable is found.
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
        log("MM", "First result has no track_id.", YELLOW)
        raise SystemExit("Musixmatch result missing track_id.")

    mm_artist = track.get("artist_name") or artist
    mm_title  = track.get("track_name") or title
    tid       = track.get("track_id")

    log("MM", f'Chosen track: "{mm_artist} - {mm_title}" (track_id={tid})', GREEN)
    return {
        "track_id": tid,
        "artist": mm_artist,
        "title": mm_title,
    }


def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    """
    Given a Musixmatch track_id, fetch the lyrics.
    Fails with SystemExit if lyrics are missing.
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
        raise SystemExit("Musixmatch track.lyrics.get failed.")

    body = data.get("message", {}).get("body", {})
    lyrics_obj = body.get("lyrics", {})
    lyrics_text = lyrics_obj.get("lyrics_body")

    if not lyrics_text or not lyrics_text.strip():
        log("MM", "Lyrics body missing or empty.", YELLOW)
        raise SystemExit("Lyrics not found for this track on Musixmatch.")

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics_text:
        lyrics_text = lyrics_text.split(footer, 1)[0].strip()

    log("MM", "Lyrics fetched from Musixmatch.", GREEN)
    return lyrics_text


# ─────────────────────────────────────────────
# YT-DLP AUDIO DOWNLOAD
# ─────────────────────────────────────────────
def youtube_download_mp3(search_str: str, slug: str) -> None:
    """
    Download first YouTube audio match for search_str into mp3s/<slug>.mp3.
    No metadata tricks, just fetch the audio.
    """
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")

    log("YT", f'Downloading audio for "{search_str}" as slug "{slug}"', CYAN)
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "-o",
        out_template,
        f"ytsearch1:{search_str}",
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log("YT", f"yt-dlp failed (exit {e.returncode}).", RED)
        raise SystemExit("yt-dlp audio download failed.")


# ─────────────────────────────────────────────
# INTERACTIVE ARTIST/TITLE PROMPTS
# ─────────────────────────────────────────────
def prompt_artist_title(query: str) -> tuple[str, str]:
    """
    Ask user explicitly for artist and title.

    Small convenience:
    - If user leaves both blank and query looks like 'Artist - Title',
      we split on ' - ' once.

    Otherwise we require non-empty answers.
    """
    print()
    print("We need artist and title for Musixmatch.")
    print(f'Base query: "{query}"')
    print("If you just press ENTER for both, and the query looks like 'Artist - Title',")
    print("we'll try to split on the first ' - '.")
    print()

    try:
        artist_in = input("Artist (ENTER to maybe infer from query): ").strip()
    except EOFError:
        artist_in = ""

    try:
        title_in = input("Title  (ENTER to maybe infer from query): ").strip()
    except EOFError:
        title_in = ""

    if not artist_in and not title_in and " - " in query:
        left, right = query.split(" - ", 1)
        artist_in = left.strip()
        title_in  = right.strip()

    if not artist_in or not title_in:
        raise SystemExit("Artist and title are required and could not be inferred from query.")

    return artist_in, title_in


# ─────────────────────────────────────────────
# ARG PARSING
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate txt+mp3 using Musixmatch (lyrics) + yt-dlp (audio)."
    )
    p.add_argument(
        "query",
        nargs="+",
        help="Search query (used for logging / optional inference), e.g. 'Artist - Title'",
    )
    return p.parse_args(argv)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    query = " ".join(args.query).strip()

    log("MODE", f'txt+mp3 generation for "{query}"', CYAN)

    mm_api_key = load_mm_env()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # Prompt user for artist/title (with tiny inference)
    artist, title = prompt_artist_title(query)

    # 1) Musixmatch: track metadata (artist/title/track_id)
    track_info = musixmatch_search_track(artist, title, mm_api_key)
    mm_artist = track_info["artist"]
    mm_title  = track_info["title"]
    track_id  = track_info["track_id"]

    # 2) Musixmatch: lyrics
    lyrics_text = musixmatch_fetch_lyrics(track_id, mm_api_key)

    # 3) Slug from Musixmatch's title
    slug = slugify(mm_title)
    log("SLUG", f'Title slug: "{slug}"', GREEN)

    txt_path  = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path  = MP3_DIR / f"{slug}.mp3"

    # 4) Download audio via YouTube using simple "artist title" search
    search_str = f"{mm_artist} {mm_title}".strip()
    youtube_download_mp3(search_str, slug)

    # 5) Write lyrics txt (always)
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote lyrics txt to {txt_path}", GREEN)

    # 6) Write meta
    meta = {
        "slug": slug,
        "query": query,
        "artist": mm_artist,
        "title": mm_title,
        "musixmatch_track_id": track_id,
        "lyrics_source": "musixmatch",
        "audio_source": "yt-dlp",
        "youtube_search_query": search_str,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("META", f"Wrote meta JSON to {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"Audio mp3 is at {mp3_path}", GREEN)
    else:
        log("MP3", f"Expected mp3 at {mp3_path} but file not found.", YELLOW)


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
