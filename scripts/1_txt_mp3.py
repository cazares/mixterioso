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
import re
import time
import unicodedata
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS,
)

TXT_DIR  = PATHS["txt"]
MP3_DIR  = PATHS["mp3"]
META_DIR = PATHS["meta"]
TIMINGS_DIR = PATHS["timings"]

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
def load_mm_env() -> str:
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not mm_api_key:
        log("ENV", "Missing Musixmatch API key.", RED)
        return ""
    return mm_api_key

# ─────────────────────────────────────────────
# NORMALIZATION
# ─────────────────────────────────────────────
def normalize(s: str) -> str:
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s

# ─────────────────────────────────────────────
# MUSIXMATCH (PLAIN LYRICS FALLBACK)
# ─────────────────────────────────────────────
def musixmatch_search_track(artist: str, title: str, api_key: str) -> dict:
    params = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "page_size": 1,
        "q_artist": artist,
        "q_track": title,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search"
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}
    tracks = data.get("message", {}).get("body", {}).get("track_list", [])
    if not tracks:
        return {}
    t = tracks[0]["track"]
    return {
        "track_id": t.get("track_id"),
        "artist": t.get("artist_name", artist),
        "title":  t.get("track_name", title),
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
    lyrics = data.get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body", "")
    if not lyrics:
        return ""
    footer = "******* This Lyrics is NOT for Commercial use *******"
    return lyrics.split(footer)[0].strip()

# ─────────────────────────────────────────────
# ROBUST LRC FETCH (SPANISH-HEAVY)
# ─────────────────────────────────────────────
LRC_SOURCES = [
    ("lrclib", "https://lrclib.net/api/get?artist_name={artist}&track_name={title}", "json"),
    ("lyricsify", "https://www.lyricsify.com/{artist}/{title}", "html"),
    ("lrcget", "https://lrc-get.vercel.app/search?song={artist}+{title}", "html"),
    ("netease_proxy", "https://music.163.com/api/search/pc?s={artist}+{title}&type=1", "netease"),
]

def try_fetch_lrc(artist: str, title: str):
    n_artist = quote_plus(normalize(artist))
    n_title  = quote_plus(normalize(title))
    failures = []

    for name, tmpl, mode in LRC_SOURCES:
        url = tmpl.format(artist=n_artist, title=n_title)
        log("LRC", f"Trying {name}: {url}", CYAN)
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
        except Exception as e:
            failures.append((name, f"HTTP error: {e}"))
            continue

        try:
            if mode == "json":
                data = r.json()
                lrc = data.get("syncedLyrics") or data.get("lyrics")
                if lrc and "[00:" in lrc:
                    log("LRC", f"{name} returned synced lyrics", GREEN)
                    return name, lrc

            elif mode == "html":
                matches = re.findall(r'href="([^"]+\.lrc)"', r.text, re.IGNORECASE)
                for link in matches[:3]:
                    try:
                        rr = requests.get(link, timeout=10)
                        rr.raise_for_status()
                        if "[00:" in rr.text:
                            log("LRC", f"{name} fetched LRC link", GREEN)
                            return name, rr.text
                    except Exception:
                        pass

            elif mode == "netease":
                data = r.json()
                songs = data.get("result", {}).get("songs", [])
                if songs:
                    sid = songs[0]["id"]
                    lrc_url = f"https://music.163.com/api/song/lyric?id={sid}&lv=1"
                    rr = requests.get(lrc_url, timeout=10)
                    rr.raise_for_status()
                    lrc = rr.json().get("lrc", {}).get("lyric")
                    if lrc and "[00:" in lrc:
                        log("LRC", "NetEase returned synced lyrics", GREEN)
                        return name, lrc
        except Exception as e:
            failures.append((name, f"Parse error: {e}"))

        failures.append((name, "No usable LRC found"))

    log("LRC", "All LRC sources failed:", RED)
    for n, r in failures:
        log("LRC", f"{n}: {r}", YELLOW)
    return None, None

# ─────────────────────────────────────────────
# YT-DLP
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# ARG PARSE
# ─────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Step1: TXT + MP3 generation (robust LRC)")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--slug", required=True)
    return p.parse_args()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    args = parse_args()
    artist = args.artist.strip()
    title  = args.title.strip()
    slug   = args.slug.strip()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

    lyrics_text = ""
    lyrics_source = "none"

    # Try synced LRC first (best)
    src, lrc = try_fetch_lrc(artist, title)
    if lrc:
        (TIMINGS_DIR / f"{slug}.lrc").write_text(lrc, encoding="utf-8")
        lyrics_source = f"lrc:{src}"
        log("LRC", f"Wrote raw LRC for {slug}", GREEN)
    else:
        # Fallback: Musixmatch plain lyrics
        mm_key = load_mm_env()
        if mm_key:
            track = musixmatch_search_track(artist, title, mm_key)
            if track and track.get("track_id"):
                lyrics_text = musixmatch_fetch_lyrics(track["track_id"], mm_key)
                if lyrics_text:
                    lyrics_source = "musixmatch"

    # Write TXT (possibly empty)
    txt_path = TXT_DIR / f"{slug}.txt"
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote {txt_path}", GREEN)

    # Download audio
    youtube_download_mp3(artist, title, slug)

    # Write META
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
