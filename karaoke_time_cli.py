#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_cli.py — Unified command-line wrapper for Karaoke Time
Author: Miguel Cázares

Now supports local audio OR YouTube URLs.
"""

import argparse
import sys
import os
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv


# ───────────────────────────────────────────────────────────────
# Helper functions
# ───────────────────────────────────────────────────────────────
def error(msg: str):
    print(f"❌ {msg}")
    sys.exit(1)


def warn(msg: str):
    print(f"⚠️  {msg}")


def info(msg: str):
    print(f"ℹ️  {msg}")


def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower().startswith("y")
    except KeyboardInterrupt:
        print()
        sys.exit(1)


# ───────────────────────────────────────────────────────────────
# Argument parsing
# ───────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="🎤 Karaoke Time — friendly CLI for lyric video generation"
)

parser.add_argument("--base-filename", help="Base name for input/output files (auto-fills related paths)")

parser.add_argument("--input-audio", help="Path to input audio file (.mp3 or .wav)")
parser.add_argument("--input-url", help="YouTube URL to download and process")
parser.add_argument("--input-lyrics-text", help="Path to lyrics text file (.txt)")
parser.add_argument("--input-lyrics-timestamps", help="Path to lyrics timings file (.csv or .ass)")

parser.add_argument("--output-video", help="Path to output karaoke video file (.mp4)")
parser.add_argument("--vocals-percent", type=float, help="Vocal mix percentage (0–100)")
parser.add_argument("--no-cache", action="store_true", help="Force regeneration of Demucs stems")

args = parser.parse_args()


# ───────────────────────────────────────────────────────────────
# Load environment
# ───────────────────────────────────────────────────────────────
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ───────────────────────────────────────────────────────────────
# Base-filename expansion (non-destructive)
# ───────────────────────────────────────────────────────────────
if args.base_filename:
    base = Path(args.base_filename)
    info(f"Using base filename: {base}")
    args.input_audio = args.input_audio or str(base.with_suffix(".mp3"))
    args.input_lyrics_text = args.input_lyrics_text or str(base.with_suffix(".txt"))
    args.input_lyrics_timestamps = args.input_lyrics_timestamps or str(base.with_suffix(".ass"))
    args.output_video = args.output_video or str(base.with_suffix(".mp4"))


# ───────────────────────────────────────────────────────────────
# Input validation: audio vs URL
# ───────────────────────────────────────────────────────────────
if args.input_url and args.input_audio:
    warn("--input-url overrides --input-audio.")
    args.input_audio = None

if not args.input_url and not args.input_audio:
    error("You must specify either --input-audio or --input-url, "
          "or provide --base-filename to auto-fill an audio path.")

if args.input_url:
    parsed = urlparse(args.input_url)
    if "youtube.com" not in parsed.netloc and "youtu.be" not in parsed.netloc:
        error("Invalid URL: only YouTube links are supported for now.")
    if not YOUTUBE_API_KEY:
        error("Missing YOUTUBE_API_KEY in .env — required for --input-url.")
    info("YouTube URL detected and validated.")
    info(f"API key loaded: {'✅' if YOUTUBE_API_KEY else '❌'}")
    # Placeholder: your fetcher/downloader script call here
    print("ℹ️  (stub) Would now download + extract audio using existing fetch_lyrics.py or yt_dlp logic.")
else:
    audio_path = Path(args.input_audio)
    if not audio_path.exists():
        error(f"Input audio file not found: {audio_path}")


# ───────────────────────────────────────────────────────────────
# Lyrics validation
# ───────────────────────────────────────────────────────────────
if not args.input_lyrics_text and not args.input_lyrics_timestamps:
    error("You must specify either --input-lyrics-text (for interactive mode) "
          "or --input-lyrics-timestamps (for non-interactive mode).")

if args.input_lyrics_text and args.input_lyrics_timestamps:
    print("⚠️  Both --input-lyrics-text and --input-lyrics-timestamps were provided.")
    print("    1. Use interactive mode (generate new CSV from text)")
    print("    2. Use existing timings (non-interactive)")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        args.input_lyrics_timestamps = None
    elif choice == "2":
        args.input_lyrics_text = None
    else:
        error("Invalid choice. Please re-run and choose 1 or 2.")

if args.input_lyrics_text and not Path(args.input_lyrics_text).exists():
    error(f"Lyrics text file not found: {args.input_lyrics_text}")

if args.input_lyrics_timestamps and not Path(args.input_lyrics_timestamps).exists():
    error(f"Timings file not found: {args.input_lyrics_timestamps}")


# ───────────────────────────────────────────────────────────────
# Vocals
# ───────────────────────────────────────────────────────────────
if args.vocals_percent is None:
    print()
    print("🎚️  No --vocals-percent specified.")
    print("    1. Full vocals (100%)")
    print("    2. No vocals (0%)")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        args.vocals_percent = 100.0
    elif choice == "2":
        args.vocals_percent = 0.0
    else:
        error("Invalid choice. Please re-run and choose 1 or 2.")

if not (0.0 <= args.vocals_percent <= 100.0):
    error("--vocals-percent must be between 0 and 100.")


# ───────────────────────────────────────────────────────────────
# Output validation
# ───────────────────────────────────────────────────────────────
if not args.output_video:
    error("You must specify --output-video or --base-filename to auto-fill it.")


# ───────────────────────────────────────────────────────────────
# Summary
# ───────────────────────────────────────────────────────────────
print("\n🎬 Karaoke Time job summary:")
print(f"   Input audio:          {args.input_audio or '(from YouTube)'}")
print(f"   Input URL:            {args.input_url or '(none)'}")
print(f"   Lyrics text:          {args.input_lyrics_text or '(none)'}")
print(f"   Lyrics timestamps:    {args.input_lyrics_timestamps or '(none)'}")
print(f"   Output video:         {args.output_video}")
print(f"   Vocals percent:       {args.vocals_percent}%")
print(f"   Force no-cache:       {'Yes' if args.no_cache else 'No'}")
print()

print("✅ Validation complete.")
print("ℹ️  Next step: connect to downloader + processing scripts.")
sys.exit(0)
