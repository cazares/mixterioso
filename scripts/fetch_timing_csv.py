#!/usr/bin/env python3

import os
import csv
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# -----------------------
# PATHS
# -----------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TIMINGS_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "timings"))

# -----------------------
# CONFIG
# -----------------------

SOURCES = [
    # LRCLIB (best source when available)
    lambda artist, title: (
        "lrclib",
        "https://lrclib.net/api/get",
        {"artist_name": artist, "track_name": title},
        "json",
    ),

    # MiniLyrics-style mirrors (best-effort scrape)
    lambda artist, title: (
        "minilyrics",
        f"https://www.minilyrics.com/search?q={artist}+{title}",
        None,
        "html",
    ),

    # Syair.info fallback
    lambda artist, title: (
        "syair",
        f"https://www.syair.info/search/{artist}+{title}",
        None,
        "html",
    ),
]

HEADERS = {
    "User-Agent": "Mixterioso/1.0 (karaoke timing fetcher)"
}

# -----------------------
# LRC PARSING
# -----------------------

LRC_LINE_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")

def parse_lrc(lrc_text):
    rows = []
    index = 0

    for line in lrc_text.splitlines():
        m = LRC_LINE_RE.match(line.strip())
        if not m:
            continue

        minutes = int(m.group(1))
        seconds = float(m.group(2))
        text = m.group(3).strip()

        time_secs = minutes * 60 + seconds

        rows.append({
            "line_index": index,
            "time_secs": round(time_secs, 3),
            "text": text,
        })
        index += 1

    return rows

# -----------------------
# FETCHERS
# -----------------------

def fetch_source(source_fn, artist, title):
    name, url, params, mode = source_fn(artist, title)

    try:
        if mode == "json":
            r = requests.get(url, params=params, headers=HEADERS, timeout=8)
            r.raise_for_status()
            data = r.json()
            lrc = data.get("syncedLyrics")
            if lrc:
                return name, lrc

        else:
            r = requests.get(url, headers=HEADERS, timeout=8)
            r.raise_for_status()
            if "[00:" in r.text:
                return name, r.text

    except Exception:
        pass

    return None

# -----------------------
# MAIN
# -----------------------

def fetch_timing_csv(artist, title, slug):
    os.makedirs(TIMINGS_DIR, exist_ok=True)

    lrc_text = None
    source_used = None

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = [
            executor.submit(fetch_source, src, artist, title)
            for src in SOURCES
        ]

        for future in as_completed(futures):
            result = future.result()
            if result:
                source_used, lrc_text = result
                break

    if not lrc_text:
        raise RuntimeError("No LRC found from any source")

    rows = parse_lrc(lrc_text)

    if not rows:
        raise RuntimeError("LRC found but contained no valid timestamped lines")

    out_path = os.path.join(TIMINGS_DIR, f"{slug}.csv")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["line_index", "time_secs", "text"]
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Wrote {len(rows)} lines → {out_path}")
    print(f"[SRC] {source_used}")

# -----------------------
# CLI
# -----------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 4:
        print("Usage: python3 scripts/fetch_timing_csv.py <artist> <title> <slug>")
        sys.exit(1)

    fetch_timing_csv(
        artist=sys.argv[1],
        title=sys.argv[2],
        slug=sys.argv[3],
    )

# end of fetch_timing_csv.py

