#!/usr/bin/env python3
# youtube_audio_picker.py
# Minimal: grab FIRST, most relevant YouTube result and save audio.
# Usage:
#   python3 scripts/youtube_audio_picker.py \
#       --query "Jesus Adrian Romero Me Dice Que Me Ama" \
#       --out songs/auto_jesus-adrian-romero-me-dice-que-me-ama.mp3
#
# Notes:
# - uses yt-dlp "ytsearch1:<query>" → sorted by relevance
# - if accent version fails, tries de-accented version once
# - macOS-friendly, python3/pip3 friendly

import argparse
import os
import re
import subprocess
import sys


def deaccent_keep_spaces(s: str) -> str:
    # crude but works for es/mx for this workflow
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunaeiouun")
    return s.translate(table)


def have_yt_dlp() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def install_yt_dlp():
    print("[youtube] yt-dlp not found; attempting to install with pip3 ...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
        return True
    except Exception as e:
        print(f"[youtube] install failed: {e}")
        return False


def download_one(query: str, out_path: str) -> bool:
    # ensure dir
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # build yt-dlp cmd
    # ytsearch1:<query> → first result, relevance
    yq = f"ytsearch1:{query}"
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestaudio/best",
        "-o", out_path,
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        yq,
    ]
    print(f"[youtube] trying query: {query!r}")
    try:
        subprocess.check_call(cmd)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[youtube] yt-dlp failed for {query!r}: {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="search query: artist + title")
    ap.add_argument("--out", required=True, help="output file (.mp3)")
    args = ap.parse_args()

    if not have_yt_dlp():
        ok = install_yt_dlp()
        if not ok:
            sys.exit(1)

    # 1) try original (relevance)
    if download_one(args.query, args.out):
      print(f"[youtube] saved to {args.out}")
      return

    # 2) fallback: de-accented
    plain_q = deaccent_keep_spaces(args.query)
    if plain_q != args.query:
        print("[youtube] retrying with de-accented query…")
        if download_one(plain_q, args.out):
            print(f"[youtube] saved to {args.out}")
            return

    print("[youtube] could not download audio for this query — aborting.")
    sys.exit(1)


if __name__ == "__main__":
    main()
# end of youtube_audio_picker.py
