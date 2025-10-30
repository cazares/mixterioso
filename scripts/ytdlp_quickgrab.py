#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ytdlp_quickgrab.py — non-interactive YouTube -> mp3
Usage:
    python3 scripts/ytdlp_quickgrab.py --query "John Frusciante Firm Kick" --out /path/to/file.mp3
"""

import argparse
import os
import sys

try:
    import yt_dlp
except ImportError:
    print("[ERROR] yt-dlp is not installed. Install with: pip3 install yt-dlp")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="YouTube search query, e.g. 'Red Hot Chili Peppers Californication'")
    ap.add_argument("--out", required=True, help="Output mp3 path")
    args = ap.parse_args()

    query = args.query.strip()
    out_path = os.path.abspath(args.out)

    out_dir = os.path.dirname(out_path)
    os.makedirs(out_dir, exist_ok=True)

    # yt-dlp needs a template; we'll download to a temp in same dir, then rename
    tmp_template = os.path.join(out_dir, "yt_tmp_%(title).200s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": tmp_template,
        "noplaylist": True,
        "quiet": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    search_url = f"ytsearch1:{query}"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=True)
    except Exception as e:
        print(f"[ERROR] yt-dlp failed for query: {query}")
        print(e)
        sys.exit(2)

    # figure out what ytdlp wrote
    # when using FFmpegExtractAudio, it will output .mp3 in the same directory
    # so let's find the newest .mp3 in out_dir starting with yt_tmp_
    candidates = [f for f in os.listdir(out_dir) if f.startswith("yt_tmp_") and f.lower().endswith(".mp3")]
    if not candidates:
        print("[ERROR] Could not find mp3 after download.")
        sys.exit(3)

    candidates.sort(key=lambda n: os.path.getmtime(os.path.join(out_dir, n)), reverse=True)
    latest = os.path.join(out_dir, candidates[0])

    # move to requested name
    os.replace(latest, out_path)
    print(f"[OK] Downloaded mp3 to {out_path}")


if __name__ == "__main__":
    main()
# end of ytdlp_quickgrab.py