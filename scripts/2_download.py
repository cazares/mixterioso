#!/usr/bin/env python3
# scripts/2_download.py
#
# OPTION A: Three independent tasks:
#   --task lyrics   (requires --query)
#   --task meta     (requires --slug OR --query)
#   --task mp3      (requires --slug)
#
# Outputs one JSON object on the FINAL line of stdout.
# All other output is prefixed + colored for 0_master.py live streaming.
#
# JSON schemas:
#
# LYRICS:
# {
#   "ok": true,
#   "slug": "...",
#   "lyrics_path": "txts/<slug>.txt"
# }
#
# META:
# {
#   "ok": true,
#   "slug": "...",
#   "artist": "...",
#   "title": "...",
#   "meta_path": "meta/<slug>.json"
# }
#
# MP3:
# {
#   "ok": true,
#   "slug": "...",
#   "mp3_path": "mp3s/<slug>.mp3",
#   "video_id": "XXXXXXXXX"
# }
#

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

RESET="\033[0m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
BLUE="\033[34m"

BASE = Path(__file__).resolve().parent.parent
TXT_DIR   = BASE / "txts"
MP3_DIR   = BASE / "mp3s"
META_DIR  = BASE / "meta"
TMP_DIR   = BASE / "tmp"

# Ensure dirs exist
TXT_DIR.mkdir(parents=True, exist_ok=True)
MP3_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# Load .env automatically (if present) so we don't rely on `source .env`
try:
    from dotenv import load_dotenv
    env_path = BASE / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    # If python-dotenv is not installed or anything else fails, just continue.
    pass


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


def slugify(text):
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:200] or "song"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# MUSIXMATCH HELPERS (TEXT LYRICS)
# ----------------------------------------------------------------------
def musixmatch_search_track(query, api_key):
    """
    Returns track_id or None
    """
    import urllib.parse
    import urllib.request

    url = (
        "https://api.musixmatch.com/ws/1.1/track.search?"
        + urllib.parse.urlencode({
            "q_track": query,
            "page_size": 1,
            "s_track_rating": "desc",
            "apikey": api_key
        })
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lst = data.get("message", {}).get("body", {}).get("track_list", [])
        if lst:
            return lst[0]["track"]["track_id"]
    except Exception:
        return None

    return None


def musixmatch_fetch_lyrics(track_id, api_key):
    """
    Returns text lyrics or None
    """
    import urllib.parse
    import urllib.request

    url = (
        "https://api.musixmatch.com/ws/1.1/track.lyrics.get?"
        + urllib.parse.urlencode({
            "track_id": track_id,
            "apikey": api_key
        })
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lyr = data.get("message", {}).get("body", {}).get("lyrics", {})
        text = lyr.get("lyrics_body")
        if text:
            # Musixmatch returns a disclaimer at the bottom — strip it.
            text = re.sub(r"\*\*\*.+", "", text).strip()
            return text
    except Exception:
        return None

    return None


# ----------------------------------------------------------------------
# LYRICS FETCH (MUSIXMATCH FIRST, THEN GENIUS)
# ----------------------------------------------------------------------
def fetch_lyrics(query, language):
    section = "Lyrics"
    slug = slugify(query)

    log(section, f"Searching lyrics for query: {query}")

    # ---------------------------------------------------------------
    # 1) FIRST PRIORITY → MUSIXMATCH TEXT-ONLY LYRICS
    # ---------------------------------------------------------------
    mm_api_key = os.getenv("MUSIXMATCH_API_KEY", "")
    if mm_api_key:
        log(section, "Trying Musixmatch...", BLUE)
        track_id = musixmatch_search_track(query, mm_api_key)

        if track_id:
            log(section, f"Musixmatch track_id={track_id}", GREEN)
            mm_lyrics = musixmatch_fetch_lyrics(track_id, mm_api_key)
            if mm_lyrics:
                log(section, "Musixmatch lyrics found.", GREEN)
                lyr_path = TXT_DIR / f"{slug}.txt"
                lyr_path.write_text(mm_lyrics, encoding="utf-8")
                return {
                    "ok": True,
                    "slug": slug,
                    "lyrics_path": str(lyr_path)
                }
        else:
            log(section, "Musixmatch found no track match.", YELLOW)
    # If MUSIXMATCH_API_KEY is not set or Musixmatch fails, we silently fall through to Genius.

    # ---------------------------------------------------------------
    # 2) FALLBACK → GENIUS (existing working behavior)
    # ---------------------------------------------------------------
    log(section, "Trying Genius fallback...", BLUE)

    lyrics = None
    try:
        import lyricsgenius
        G = lyricsgenius.Genius(
            os.getenv("GENIUS_API_KEY", ""),
            timeout=10,
            skip_non_songs=True
        )
        song = G.search_song(query)
        if song and song.lyrics:
            log(section, "Genius lyrics found.", GREEN)
            lyrics = song.lyrics
    except Exception as e:
        log(section, f"Genius API failed: {e}", YELLOW)

    # ---------------------------------------------------------------
    # 3) FALLBACK OF LAST RESORT → manual placeholder
    # ---------------------------------------------------------------
    if not lyrics:
        log(section, "No lyrics from Musixmatch or Genius.", YELLOW)
        lyrics = f"[NO LYRICS FOUND]\n\n(Please update txts/{slug}.txt manually)"

    lyr_path = TXT_DIR / f"{slug}.txt"
    lyr_path.write_text(lyrics, encoding="utf-8")

    return {
        "ok": True,
        "slug": slug,
        "lyrics_path": str(lyr_path)
    }


# ----------------------------------------------------------------------
# META FETCH
# ----------------------------------------------------------------------
def fetch_meta(slug, query=None):
    section = "Meta"

    artist = ""
    title = ""

    # Simple heuristic: try parsing “artist - title”
    if query and "-" in query:
        parts = [p.strip() for p in query.split("-", 1)]
        if len(parts) == 2:
            artist, title = parts

    # If not, fallback guess
    if not artist or not title:
        if query:
            tokens = query.split()
            if len(tokens) >= 2:
                artist = tokens[0]
                title = " ".join(tokens[1:])

    if not artist:
        artist = "Unknown Artist"
    if not title:
        title = slug.replace("_", " ").title()

    meta_path = META_DIR / f"{slug}.json"
    write_json(meta_path, {"artist": artist, "title": title})

    log(section, f"Artist={artist}, Title={title}", GREEN)
    return {
        "ok": True,
        "slug": slug,
        "artist": artist,
        "title": title,
        "meta_path": str(meta_path)
    }


# ----------------------------------------------------------------------
# MP3 DOWNLOAD
# ----------------------------------------------------------------------
def fetch_mp3(slug):
    section = "MP3"

    mp3_path = MP3_DIR / f"{slug}.mp3"
    if mp3_path.exists():
        log(section, f"MP3 already exists: {mp3_path}", GREEN)
        return {
            "ok": True,
            "slug": slug,
            "mp3_path": str(mp3_path),
            "video_id": None,
        }

    search_query = f"ytsearch1:{slug.replace('_', ' ')}"
    tmp_mp3 = TMP_DIR / f"{slug}.mp3"

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "-o", str(tmp_mp3),
        search_query
    ]

    log(section, f"Running yt-dlp: {' '.join(cmd)}")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log(section, f"yt-dlp failed: {proc.stderr}", RED)
        return {
            "ok": False,
            "error": "yt-dlp-failed",
            "stderr": proc.stderr,
            "slug": slug
        }

    tmp_mp3.rename(mp3_path)

    vid = None
    m = re.search(r"watch\?v=([A-Za-z0-9_\-]{6,})", proc.stdout)
    if m:
        vid = m.group(1)

    log(section, f"Downloaded MP3: {mp3_path}", GREEN)

    return {
        "ok": True,
        "slug": slug,
        "mp3_path": str(mp3_path),
        "video_id": vid,
    }


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=["lyrics", "mp3", "meta"])
    ap.add_argument("--slug")
    ap.add_argument("--query")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    task = args.task
    query = args.query
    slug = args.slug
    lang = args.language

    # LYRICS ----------------------------------------------------------
    if task == "lyrics":
        if not query:
            print(json.dumps({"ok": False, "error": "--query required"}))
            return
        result = fetch_lyrics(query, lang)
        print(json.dumps(result))
        return

    # META ------------------------------------------------------------
    if task == "meta":
        if not slug and not query:
            print(json.dumps({"ok": False, "error": "Need --slug OR --query"}))
            return
        if not slug:
            slug = slugify(query)
        result = fetch_meta(slug, query=query)
        print(json.dumps(result))
        return

    # MP3 -------------------------------------------------------------
    if task == "mp3":
        if not slug:
            print(json.dumps({"ok": False, "error": "--slug required"}))
            return
        result = fetch_mp3(slug)
        print(json.dumps(result))
        return


if __name__ == "__main__":
    main()

# end of 2_download.py
