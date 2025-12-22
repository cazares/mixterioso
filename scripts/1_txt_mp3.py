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
import unicodedata
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    PATHS,
)

TXT_DIR     = PATHS["txt"]
MP3_DIR     = PATHS["mp3"]
META_DIR    = PATHS["meta"]
TIMINGS_DIR = PATHS["timings"]

# ─────────────────────────────────────────────
# NORMALIZATION / QUERY VARIANTS
# ─────────────────────────────────────────────
def normalize(s: str) -> str:
    s = s.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s

def query_variants(artist: str, title: str):
    raw_artist = artist.strip()
    raw_title  = title.strip()
    norm_artist = normalize(artist)
    norm_title  = normalize(title)

    variants = [
        (raw_artist, raw_title),
        (norm_artist, norm_title),
        ("", raw_title),
        ("", norm_title),
    ]

    seen = set()
    out = []
    for a, t in variants:
        key = (a.lower(), t.lower())
        if key not in seen:
            seen.add(key)
            out.append((a, t))
    return out

# ─────────────────────────────────────────────
# LRCLIB SEARCH-FIRST
# ─────────────────────────────────────────────
def lrclib_search(artist: str, title: str):
    url = "https://lrclib.net/api/search"
    params = {"artist_name": artist, "track_name": title}
    log("LRCLIB", f"Search params={params}", CYAN)
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        log("LRCLIB", f"Search returned {len(data)} candidates", CYAN)
        return data
    except Exception as e:
        log("LRCLIB", f"Search failed: {e}", YELLOW)
        return []

def lrclib_fetch(entry: dict):
    track_id = entry.get("id")
    if not track_id:
        return None
    url = f"https://lrclib.net/api/get/{track_id}"
    log("LRCLIB", f"Fetching track_id={track_id}", CYAN)
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        lrc = data.get("syncedLyrics")
        if lrc and "[00:" in lrc:
            return lrc
    except Exception as e:
        log("LRCLIB", f"Fetch failed: {e}", YELLOW)
    return None

# ─────────────────────────────────────────────
# NETEASE (MULTI-CANDIDATE)
# ─────────────────────────────────────────────
def netease_try(artist: str, title: str):
    q = quote_plus(f"{artist} {title}".strip())
    url = f"https://music.163.com/api/search/pc?s={q}&type=1"
    log("NETEASE", f"Search URL={url}", CYAN)
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("NETEASE", f"Search failed: {e}", YELLOW)
        return None

    songs = data.get("result", {}).get("songs", [])
    log("NETEASE", f"Found {len(songs)} candidates", CYAN)

    for s in songs[:5]:
        name = s.get("name")
        artists = ", ".join(a.get("name") for a in s.get("artists", []))
        sid = s.get("id")
        log("NETEASE", f"Candidate: {artists} – {name} (id={sid})", CYAN)
        try:
            lr = requests.get(
                f"https://music.163.com/api/song/lyric?id={sid}&lv=1",
                timeout=10,
            )
            lr.raise_for_status()
            lrc = lr.json().get("lrc", {}).get("lyric")
            if lrc and "[00:" in lrc:
                return lrc
        except Exception:
            continue
    return None

# ─────────────────────────────────────────────
# KUGOU (LRC ONLY – LOW/MID COMPLEXITY)
# ─────────────────────────────────────────────
def kugou_try(artist: str, title: str):
    q = quote_plus(f"{artist} {title}".strip())
    search_url = f"https://lyrics.kugou.com/search?keyword={q}&ver=1&man=yes&client=pc"
    log("KUGOU", f"Search URL={search_url}", CYAN)
    try:
        r = requests.get(search_url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("KUGOU", f"Search failed: {e}", YELLOW)
        return None

    candidates = data.get("candidates", [])
    log("KUGOU", f"Found {len(candidates)} candidates", CYAN)

    for c in candidates[:5]:
        lid = c.get("id")
        acc = c.get("accesskey")
        if not lid or not acc:
            continue
        try:
            lrc_url = (
                f"https://lyrics.kugou.com/download?ver=1&client=pc"
                f"&id={lid}&accesskey={acc}&fmt=lrc"
            )
            lr = requests.get(lrc_url, timeout=10)
            lr.raise_for_status()
            lrc = lr.json().get("content")
            if lrc and "[00:" in lrc:
                return lrc
        except Exception:
            continue
    return None

# ─────────────────────────────────────────────
# MASTER LRC FETCH
# ─────────────────────────────────────────────
def try_fetch_lrc(artist: str, title: str, slug: str):
    for qa, qt in query_variants(artist, title):
        log("LRC", f"Query variant → artist='{qa}' title='{qt}'", CYAN)

        # 1) LRCLIB
        for entry in lrclib_search(qa, qt)[:5]:
            log("LRCLIB", f"Candidate: {entry.get('artistName')} – {entry.get('trackName')}", CYAN)
            lrc = lrclib_fetch(entry)
            if lrc:
                out = TIMINGS_DIR / f"{slug}.lrc"
                out.write_text(lrc, encoding="utf-8")
                log("SUCCESS", f"TIMED LYRICS FOUND via LRCLIB → {out}", GREEN)
                return "lrclib", out

        # 2) NETEASE
        lrc = netease_try(qa, qt)
        if lrc:
            out = TIMINGS_DIR / f"{slug}.lrc"
            out.write_text(lrc, encoding="utf-8")
            log("SUCCESS", f"TIMED LYRICS FOUND via NETEASE → {out}", GREEN)
            return "netease", out

        # 3) KUGOU
        lrc = kugou_try(qa, qt)
        if lrc:
            out = TIMINGS_DIR / f"{slug}.lrc"
            out.write_text(lrc, encoding="utf-8")
            log("SUCCESS", f"TIMED LYRICS FOUND via KUGOU → {out}", GREEN)
            return "kugou", out

    log("LRC", "All timed-lyrics sources exhausted.", RED)
    return None, None

# ─────────────────────────────────────────────
# MUSIXMATCH (PLAIN FALLBACK)
# ─────────────────────────────────────────────
def load_mm_env():
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    return os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API") or ""

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
        tracks = r.json().get("message", {}).get("body", {}).get("track_list", [])
        if not tracks:
            return {}
        t = tracks[0]["track"]
        return {"track_id": t.get("track_id")}
    except Exception:
        return {}

def musixmatch_fetch_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    try:
        r = requests.get(url, params={"track_id": track_id, "apikey": api_key}, timeout=10)
        r.raise_for_status()
        lyrics = r.json().get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body", "")
        return lyrics.split("*******")[0].strip()
    except Exception:
        return ""

# ─────────────────────────────────────────────
# YT-DLP
# ─────────────────────────────────────────────
def youtube_download_mp3(artist: str, title: str, slug: str):
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    query = f"{artist} {title}".strip()
    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(MP3_DIR / f"{slug}.%(ext)s"), f"ytsearch1:{query}"],
        check=True,
    )

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()

    for d in (TXT_DIR, MP3_DIR, META_DIR, TIMINGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    lyrics_source = "none"
    src, path = try_fetch_lrc(args.artist, args.title, args.slug)
    if src:
        lyrics_source = f"lrc:{src}"
    else:
        mm = load_mm_env()
        if mm:
            t = musixmatch_search_track(args.artist, args.title, mm)
            if t.get("track_id"):
                lyrics = musixmatch_fetch_lyrics(t["track_id"], mm)
                if lyrics:
                    (TXT_DIR / f"{args.slug}.txt").write_text(lyrics, encoding="utf-8")
                    lyrics_source = "musixmatch"

    youtube_download_mp3(args.artist, args.title, args.slug)

    meta = {
        "slug": args.slug,
        "artist": args.artist,
        "title": args.title,
        "lyrics_source": lyrics_source,
    }
    (META_DIR / f"{args.slug}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    log("META", f"Wrote meta for {args.slug}", GREEN)

if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
