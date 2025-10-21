#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_cli.py — Unified command-line wrapper for Karaoke Time
Author: Miguel Cázares

Supports local audio OR YouTube URLs.
"""

import argparse
import sys
import os
from pathlib import Path
from dotenv import load_dotenv
import subprocess

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
# Base-filename expansion
# ───────────────────────────────────────────────────────────────
if args.base_filename:
    base = Path(args.base_filename)
    info(f"Using base filename: {base}")
    args.input_audio = args.input_audio or str(base.with_suffix(".mp3"))
    args.input_lyrics_text = args.input_lyrics_text or str(base.with_suffix(".txt"))
    args.input_lyrics_timestamps = args.input_lyrics_timestamps or str(base.with_suffix(".ass"))
    args.output_video = args.output_video or str(base.with_suffix(".mp4"))

# ───────────────────────────────────────────────────────────────
# Audio source handling
# ───────────────────────────────────────────────────────────────
if args.input_url:
    print("🎧 You provided a YouTube URL.\n")
    print("Choose how to download the audio:")
    print("  1. yt-dlp  — direct download (⚡ fastest, no API key needed, recommended)")
    print("  2. YouTube API  — uses your API key for metadata and lyric alignment (⚙️ slower, requires valid YOUTUBE_API_KEY)\n")
    choice = input("Choose 1 or 2 [default: 1]: ").strip() or "1"

    if choice == "1":
        mp3_path = Path(f"songs/{args.base_filename}.mp3")
        if mp3_path.exists():
            print(f"🎵 Reusing existing audio file: {mp3_path}")
            args.input_audio = str(mp3_path)
        else:
            print("▶ Using yt-dlp (no API key required)...")
            os.makedirs("songs", exist_ok=True)
            result = subprocess.run([
                "yt-dlp", "-x", "--audio-format", "mp3",
                "--extractor-args", "youtube:player_client=android",
                "-o", f"songs/{args.base_filename}.%(ext)s",
                args.input_url
            ])
            if result.returncode == 0:
                args.input_audio = str(mp3_path)
                print("✅ Download complete via yt-dlp.")
            else:
                error("yt-dlp failed or was interrupted. Re-run and choose option 2 as fallback.")

    elif choice == "2":
        error("YouTube API mode not yet implemented for direct URL download.")
    else:
        error("Invalid choice. Aborting.")

# ───────────────────────────────────────────────────────────────
# Determine mode
# ───────────────────────────────────────────────────────────────
mode = None
if args.input_lyrics_text and args.input_lyrics_timestamps:
    choice = input("⚠️  Both provided. 1=interactive (text), 2=non-interactive (timings) [default: 1]: ").strip() or "1"
    if choice == "1":
        args.input_lyrics_timestamps = None
        mode = "interactive"
    else:
        args.input_lyrics_text = None
        mode = "timings"
elif args.input_lyrics_text:
    mode = "interactive"
elif args.input_lyrics_timestamps:
    mode = "timings"
else:
    error("You must specify --input-lyrics-text (interactive) or --input-lyrics-timestamps (non-interactive).")

# ───────────────────────────────────────────────────────────────
# Vocals selection
# ───────────────────────────────────────────────────────────────
if args.vocals_percent is None:
    print("\n🎚️  No --vocals-percent specified.")
    print("    1. Full vocals (100%)")
    print("    2. No vocals (0%)")
    choice = input("Choose 1 or 2 [default: 1]: ").strip() or "1"
    args.vocals_percent = 100.0 if choice == "1" else 0.0

if not (0.0 <= args.vocals_percent <= 100.0):
    error("--vocals-percent must be between 0 and 100.")

if not args.output_video:
    error("You must specify --output-video or --base-filename to auto-fill it.")

# ───────────────────────────────────────────────────────────────
# Interactive Mode
# ───────────────────────────────────────────────────────────────
if mode == "interactive":
    print("🎤 Launching interactive lyric timing mode...")
    artist = input("Enter artist name: ").strip() or "Unknown_Artist"
    title = input("Enter song title: ").strip() or (Path(args.base_filename).stem if args.base_filename else "Unknown_Title")

    lyrics_dir = Path("lyrics")
    lyrics_dir.mkdir(exist_ok=True)
    lyrics_path = lyrics_dir / f"{artist.replace(' ', '_')}_{title.replace(' ', '_')}.txt"

    if not lyrics_path.exists():
        print(f"❌ Lyrics file not found: {lyrics_path}")
        print("💡 Created empty lyrics file for you.")
        lyrics_path.touch()

    print(f"📝 Open {lyrics_path} in your editor, paste lyrics, save, and press [Enter] once done.")
    while True:
        try:
            input("⏸️  Waiting… press [Enter] after saving: ")
        except KeyboardInterrupt:
            print()
            error("Interrupted by user.")
        if lyrics_path.exists() and lyrics_path.stat().st_size > 0:
            print("✅ Lyrics file detected and not empty.")
            break
        print("⚠️  File still missing or empty. Save and press [Enter] again.")

    print("🎬 Starting lyric tapper…")
    subprocess.run([
        "python3", "scripts/karaoke_auto_sync_lyrics.py",
        "--artist", artist,
        "--title", title,
        "--vocals-percent", str(args.vocals_percent),
        "--interactive"
    ], check=True)

# ───────────────────────────────────────────────────────────────
# Non-interactive Mode
# ───────────────────────────────────────────────────────────────
elif mode == "timings":
    if not Path(args.input_lyrics_timestamps).exists():
        error(f"Timings file not found: {args.input_lyrics_timestamps}")
    print("🎬 Rendering final video...")
    subprocess.run([
        "python3", "scripts/karaoke_core.py",
        "--csv", args.input_lyrics_timestamps,
        "--mp3", args.input_audio,
        "--font-size", "140",
        "--offset", "0"
    ], check=True)

# ───────────────────────────────────────────────────────────────
# Done
# ───────────────────────────────────────────────────────────────
print("\n✅ Karaoke Time completed successfully.")
print("🎬 Output video saved as:", args.output_video)
sys.exit(0)
