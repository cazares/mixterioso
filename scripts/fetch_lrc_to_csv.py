#!/usr/bin/env python3
"""
Standalone LRC → CSV fetcher for Mixterioso.

Writes:
    ../timings/<slug>.csv
Format:
    line_index,time_secs,text
"""

import argparse
import csv
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_plus

import requests

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
TIMINGS_DIR = ROOT / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
def log(tag, msg):
    print(f"[{tag}] {msg}")

# ─────────────────────────────────────────────
# NORMALIZATION
# ─────────────────────────────────────────────
def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s

def query_variants(artist: str, title: str):
    return [
        (artist, title),
        (normalize(artist), normalize(title)),
        ("", title),
        ("", normalize(title)),
    ]

# ─────────────────────────────────────────────
# LRC → ROWS
# ─────────────────────────────────────────────
_TS = re.compile(r"\[(\d+):(\d{2})(?:\.(\d{1,3}))?\]")

def parse_lrc(lrc: str):
    rows = []
    for line in lrc.splitlines():
        stamps = list(_TS.finditer(line))
        if not stamps:
            continue

        lyric = _TS.sub("", line).strip()
        if not lyric:
            continue

        for m in stamps:
            mm = int(m.group(1))
            ss = int(m.group(2))
            frac = m.group(3) or "0"
            ms = int(frac.ljust(3, "0")[:3])
            t = mm * 60 + ss + ms / 1000
            rows.append((t, lyric))

    rows.sort(key=lambda x: x[0])
    return rows

def write_csv(rows, slug):
    path = TIMINGS_DIR / f"{slug}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "time_secs", "text"])
        for i, (t, txt) in enumerate(rows):
            w.writerow([i, f"{t:.3f}", txt])

    log("SUCCESS", f"Wrote CSV → {path}")
    log("SUCCESS", f"Lines: {len(rows)}")
    return path

# ─────────────────────────────────────────────
# SOURCES
# ─────────────────────────────────────────────
def lrclib(artist, title):
    log("LRCLIB", f"Search: {artist} / {title}")
    try:
        r = requests.get(
            "https://lrclib.net/api/search",
            params={"artist_name": artist, "track_name": title},
            timeout=10,
        )
        r.raise_for_status()
        for hit in r.json()[:5]:
            tid = hit.get("id")
            if not tid:
                continue
            r2 = requests.get(f"https://lrclib.net/api/get/{tid}", timeout=10)
            r2.raise_for_status()
            lrc = r2.json().get("syncedLyrics")
            if lrc and "[00:" in lrc:
                return lrc
    except Exception as e:
        log("LRCLIB", f"Failed: {e}")
    return None

def netease(artist, title):
    q = quote_plus(f"{artist} {title}".strip())
    log("NETEASE", f"Search: {q}")
    try:
        r = requests.get(
            f"https://music.163.com/api/search/pc?s={q}&type=1",
            timeout=10,
        )
        r.raise_for_status()
        songs = r.json().get("result", {}).get("songs", [])
        for s in songs[:5]:
            sid = s.get("id")
            if not sid:
                continue
            r2 = requests.get(
                f"https://music.163.com/api/song/lyric?id={sid}&lv=1",
                timeout=10,
            )
            r2.raise_for_status()
            lrc = r2.json().get("lrc", {}).get("lyric")
            if lrc and "[00:" in lrc:
                return lrc
    except Exception as e:
        log("NETEASE", f"Failed: {e}")
    return None

def kugou(artist, title):
    q = quote_plus(f"{artist} {title}".strip())
    log("KUGOU", f"Search: {q}")
    try:
        r = requests.get(
            f"https://lyrics.kugou.com/search?keyword={q}&client=pc",
            timeout=10,
        )
        r.raise_for_status()
        for c in r.json().get("candidates", [])[:5]:
            lid = c.get("id")
            acc = c.get("accesskey")
            if not lid or not acc:
                continue
            r2 = requests.get(
                f"https://lyrics.kugou.com/download"
                f"?id={lid}&accesskey={acc}&fmt=lrc",
                timeout=10,
            )
            r2.raise_for_status()
            lrc = r2.json().get("content")
            if lrc and "[00:" in lrc:
                return lrc
    except Exception as e:
        log("KUGOU", f"Failed: {e}")
    return None

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("slug")
    args = ap.parse_args()

    for a, t in query_variants(args.artist, args.title):
        for name, fn in [
            ("LRCLIB", lrclib),
            ("NETEASE", netease),
            ("KUGOU", kugou),
        ]:
            log("TRY", f"{name} → artist='{a}' title='{t}'")
            lrc = fn(a, t)
            if lrc:
                rows = parse_lrc(lrc)
                if rows:
                    write_csv(rows, args.slug)
                    return

    log("FAIL", "No timed lyrics found.")
    raise SystemExit(1)

if __name__ == "__main__":
    main()

# end of fetch_lrc_to_csv.py

