#!/usr/bin/env python3
import sys, subprocess, os, json, argparse
from pathlib import Path
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mix_utils import PATHS, log, GREEN, YELLOW, RED

TXT_DIR = PATHS["txt"]
MP3_DIR = PATHS["mp3"]
META_DIR = PATHS["meta"]
TIMINGS_DIR = PATHS["timings"]

def fetch_lrc(artist, title, slug):
    out = TIMINGS_DIR / f"{slug}.lrc"
    if out.exists():
        return
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=10,
        )
        if r.status_code != 200:
            return
        data = r.json()
        lrc = data.get("syncedLyrics") or ""
        if lrc.strip():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(lrc.strip(), encoding="utf-8")
            log("LRC", f"Wrote {out}", GREEN)
    except Exception:
        pass

def fetch_captions(slug, url):
    out = TIMINGS_DIR / f"{slug}.vtt"
    if out.exists():
        return
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-sub",
        "--sub-lang", "en",
        "--sub-format", "vtt",
        "-o", str(out.with_suffix("")),
        url,
    ]
    try:
        subprocess.run(cmd, check=True)
        if out.exists():
            log("CAPTION", f"Wrote {out}", GREEN)
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--youtube-url", required=False)
    args = ap.parse_args()

    artist, title, slug = args.artist, args.title, args.slug

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

    (TXT_DIR / f"{slug}.txt").write_text("", encoding="utf-8")

    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3",
         "-o", str(MP3_DIR / f"{slug}.%(ext)s"),
         f"ytsearch1:{artist} {title}"],
        check=True,
    )

    meta = {"artist": artist, "title": title, "slug": slug}
    if args.youtube_url:
        meta["youtube_url"] = args.youtube_url

    (META_DIR / f"{slug}.json").write_text(json.dumps(meta, indent=2))

    fetch_lrc(artist, title, slug)
    if args.youtube_url:
        fetch_captions(slug, args.youtube_url)

if __name__ == "__main__":
    main()
# end of 1_fetch.py
