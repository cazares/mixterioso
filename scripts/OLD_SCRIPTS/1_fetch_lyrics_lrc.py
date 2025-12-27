#!/usr/bin/env python3
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path (repo root)
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""
Fetch LRC into timings/<slug>.lrc using LRCLIB (no API key).
Best effort. If not found, leaves existing file alone; otherwise creates empty file.
"""

import argparse
import time
from pathlib import Path
import requests

try:
    from mix_utils import PATHS, log, CYAN, GREEN, YELLOW, RED
except Exception:
    def log(tag, msg, color=""):
        print(f"[{tag}] {msg}")
    CYAN = GREEN = YELLOW = RED = ""
    ROOT = Path(__file__).resolve().parent.parent
    PATHS = {"timings": ROOT / "timings"}

TIMINGS_DIR = Path(PATHS["timings"])

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    return ap.parse_args(argv)

def lrclib_search(artist: str, title: str):
    url = "https://lrclib.net/api/search"
    params = {"artist_name": artist, "track_name": title}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []

def main(argv=None):
    args = parse_args(argv)
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TIMINGS_DIR / f"{args.slug}.lrc"
    t0 = time.perf_counter()
    try:
        rows = lrclib_search(args.artist, args.title)
        lrc = ""
        for r in rows:
            if r.get("syncedLyrics"):
                lrc = (r.get("syncedLyrics") or "").strip()
                break
        if lrc:
            out_path.write_text(lrc + "\n", encoding="utf-8")
            log("lrc", f"LRC fetched → {out_path}", GREEN)
        else:
            log("lrc", "No LRC found", YELLOW)
            if not out_path.exists():
                out_path.write_text("", encoding="utf-8")
        return 0
    except Exception as e:
        log("lrc", f"failed: {e}", YELLOW)
        if not out_path.exists():
            out_path.write_text("", encoding="utf-8")
        return 0
    finally:
        log("lrc", f"dt={time.perf_counter()-t0:.2f}s", CYAN)

if __name__ == "__main__":
    raise SystemExit(main())
# end of 1_fetch_lyrics_lrc.py
