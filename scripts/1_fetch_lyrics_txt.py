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
Fetch plain lyrics to txts/<slug>.txt.

Fallback order:
1) Musixmatch (if MUSIXMATCH_API_KEY / MM_API present)
2) lyrics.ovh
Otherwise: empty scaffold.
"""

import argparse
import os
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

try:
    from mix_utils import PATHS, log, CYAN, GREEN, YELLOW, RED
except Exception:
    def log(tag, msg, color=""):
        print(f"[{tag}] {msg}")
    CYAN = GREEN = YELLOW = RED = ""
    ROOT = Path(__file__).resolve().parent.parent
    PATHS = {"txt": ROOT / "txts"}

TXT_DIR = Path(PATHS["txt"])

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    return ap.parse_args(argv)

def load_mm_key(root: Path) -> str:
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    return (os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API") or "").strip()

def mm_search_track(artist: str, title: str, api_key: str) -> int:
    url = "https://api.musixmatch.com/ws/1.1/track.search"
    params = {"apikey": api_key, "f_has_lyrics": 1, "page_size": 1, "q_artist": artist, "q_track": title}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    tracks = (data.get("message", {}).get("body", {}) or {}).get("track_list", []) or []
    if not tracks:
        return 0
    t = tracks[0].get("track") or {}
    return int(t.get("track_id") or 0)

def mm_fetch_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    lyrics = (((data.get("message", {}) or {}).get("body", {}) or {}).get("lyrics", {}) or {}).get("lyrics_body", "") or ""
    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics:
        lyrics = lyrics.split(footer)[0]
    return lyrics.strip()

def ovh_fetch(artist: str, title: str) -> str:
    if not artist:
        return ""
    url = f"https://api.lyrics.ovh/v1/{quote_plus(artist)}/{quote_plus(title)}"
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        return ""
    data = r.json()
    return (data.get("lyrics") or "").strip()

def main(argv=None):
    args = parse_args(argv)
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TXT_DIR / f"{args.slug}.txt"
    root = Path(__file__).resolve().parent.parent
    t0 = time.perf_counter()

    lyrics = ""
    source = "none"

    try:
        mm_key = load_mm_key(root)
        if mm_key and args.artist:
            try:
                tid = mm_search_track(args.artist, args.title, mm_key)
                if tid:
                    lyrics = mm_fetch_lyrics(tid, mm_key)
                    if lyrics:
                        source = "musixmatch"
            except Exception:
                pass

        if not lyrics:
            try:
                lyrics = ovh_fetch(args.artist, args.title)
                if lyrics:
                    source = "lyrics.ovh"
            except Exception:
                pass

        out_path.write_text((lyrics or ""), encoding="utf-8")
        log("txt", f"Wrote {out_path} (source={source})", GREEN if lyrics else YELLOW)
        return 0
    finally:
        log("txt", f"dt={time.perf_counter()-t0:.2f}s", CYAN)

if __name__ == "__main__":
    raise SystemExit(main())
# end of 1_fetch_lyrics_txt.py
