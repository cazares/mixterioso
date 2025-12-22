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
import csv
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
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s

def query_variants(artist: str, title: str):
    raw_artist = (artist or "").strip()
    raw_title  = (title or "").strip()
    norm_artist = normalize(artist or "")
    norm_title  = normalize(title or "")

    variants = [
        (raw_artist, raw_title),
        (norm_artist, norm_title),
        ("", raw_title),
        ("", norm_title),
    ]

    seen = set()
    out = []
    for a, t in variants:
        key = ((a or "").lower(), (t or "").lower())
        if key not in seen:
            seen.add(key)
            out.append((a, t))
    return out

# ─────────────────────────────────────────────
# LRC → CSV (CANONICAL)
# ─────────────────────────────────────────────
_LRC_TS = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")

def parse_lrc_to_rows(lrc_text: str):
    rows = []
    if not lrc_text:
        return rows

    for line in lrc_text.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            continue

        stamps = list(_LRC_TS.finditer(line))
        if not stamps:
            continue

        lyric = _LRC_TS.sub("", line).strip()
        if not lyric:
            continue

        for m in stamps:
            mm = int(m.group(1))
            ss = int(m.group(2))
            frac = m.group(3) or "0"
            if len(frac) == 1:
                ms = int(frac) * 100
            elif len(frac) == 2:
                ms = int(frac) * 10
            else:
                ms = int(frac[:3])
            t = mm * 60 + ss + ms / 1000.0
            rows.append((t, lyric))

    rows.sort(key=lambda x: x[0])
    return rows

def write_timings_csv(rows, slug: str):
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out = TIMINGS_DIR / f"{slug}.csv"

    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "time_secs", "text"])
        for i, (t, text) in enumerate(rows):
            w.writerow([i, f"{t:.3f}", text])

    log("TIMINGS", f"✔ CSV READY → {out}", GREEN)
    log("TIMINGS", f"✔ Rows: {len(rows)}", CYAN)
    return out

# ─────────────────────────────────────────────
# LRCLIB
# ─────────────────────────────────────────────
def lrclib_search(artist: str, title: str):
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"artist_name": artist, "track_name": title},
            timeout=10,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log("LRCLIB", f"Search failed: {e}", YELLOW)
        return []

def lrclib_fetch(entry: dict):
    tid = entry.get("id")
    if not tid:
        return None
    try:
        r = requests.get(f"https://lrclib.net/api/get/{tid}", timeout=10)
        r.raise_for_status()
        lrc = r.json().get("syncedLyrics")
        if lrc and "[00:" in lrc:
            return lrc
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────
# NETEASE
# ─────────────────────────────────────────────
def netease_try(artist: str, title: str):
    q = quote_plus(f"{artist} {title}".strip())
    try:
        r = requests.get(f"https://music.163.com/api/search/pc?s={q}&type=1", timeout=10)
        r.raise_for_status()
        songs = r.json().get("result", {}).get("songs", [])
    except Exception:
        return None

    for s in songs[:5]:
        sid = s.get("id")
        if not sid:
            continue
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
# KUGOU
# ─────────────────────────────────────────────
def kugou_try(artist: str, title: str):
    q = quote_plus(f"{artist} {title}".strip())
    try:
        r = requests.get(
            f"https://lyrics.kugou.com/search?keyword={q}&ver=1&client=pc",
            timeout=10,
        )
        r.raise_for_status()
        candidates = r.json().get("candidates", [])
    except Exception:
        return None

    for c in candidates[:5]:
        lid = c.get("id")
        acc = c.get("accesskey")
        if not lid or not acc:
            continue
        try:
            lr = requests.get(
                f"https://lyrics.kugou.com/download"
                f"?ver=1&client=pc&id={lid}&accesskey={acc}&fmt=lrc",
                timeout=10,
            )
            lr.raise_for_status()
            lrc = lr.json().get("content")
            if lrc and "[00:" in lrc:
                return lrc
        except Exception:
            continue
    return None

# ─────────────────────────────────────────────
# MASTER TIMED LYRICS FETCH
# ─────────────────────────────────────────────
def fetch_timed_lyrics(artist: str, title: str, slug: str):
    for qa, qt in query_variants(artist, title):
        log("LRC", f"Query → artist='{qa}' title='{qt}'", CYAN)

        for entry in lrclib_search(qa, qt)[:5]:
            lrc = lrclib_fetch(entry)
            if lrc:
                rows = parse_lrc_to_rows(lrc)
                if rows:
                    write_timings_csv(rows, slug)
                    return "lrclib"

        lrc = netease_try(qa, qt)
        if lrc:
            rows = parse_lrc_to_rows(lrc)
            if rows:
                write_timings_csv(rows, slug)
                return "netease"

        lrc = kugou_try(qa, qt)
        if lrc:
            rows = parse_lrc_to_rows(lrc)
            if rows:
                write_timings_csv(rows, slug)
                return "kugou"

    log("LRC", "No timed lyrics found.", RED)
    return None

# ─────────────────────────────────────────────
# AUDIO
# ─────────────────────────────────────────────
def youtube_download_mp3(artist: str, title: str, slug: str):
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            str(MP3_DIR / f"{slug}.%(ext)s"),
            f"ytsearch1:{artist} {title}",
        ],
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

    lyrics_source = fetch_timed_lyrics(args.artist, args.title, args.slug) or "none"
    youtube_download_mp3(args.artist, args.title, args.slug)

    meta = {
        "slug": args.slug,
        "artist": args.artist,
        "title": args.title,
        "lyrics_source": lyrics_source,
    }
    (META_DIR / f"{args.slug}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log("META", f"Wrote meta for {args.slug}", GREEN)

if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
