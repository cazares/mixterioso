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

def confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower().startswith("y")
    except KeyboardInterrupt:
        print()
        sys.exit(1)

# ───────────────────────────────────────────────────────────────
# New helper for UTF-8 normalization
# ───────────────────────────────────────────────────────────────
def clean_text(s: str) -> str:
    """Normalize and sanitize any text for safe terminal output or file usage."""
    return (
        s.encode("utf-8", "ignore")
        .decode("utf-8")
        .replace("\uFEFF", "")
        .replace("\uFFFD", "")
        .replace("\xa0", " ")
        .replace("\r", "")
        .strip()
    )

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

parser.add_argument("--offset", type=float, default=0.0, help="Shift all lyric timestamps (in seconds, can be negative)")

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
    base = Path(clean_text(args.base_filename))
    info(f"Using base filename: {base}")
    args.input_audio = args.input_audio or str(base.with_suffix(".mp3"))
    args.input_lyrics_text = args.input_lyrics_text or str(base.with_suffix(".txt"))
    args.input_lyrics_timestamps = args.input_lyrics_timestamps or str(base.with_suffix(".ass"))
    args.output_video = args.output_video or str(base.with_suffix(".mp4"))

# ───────────────────────────────────────────────────────────────
# Input validation: audio vs URL
# ───────────────────────────────────────────────────────────────
if args.input_url:
    args.input_url = clean_text(args.input_url)
    print("🎧 You provided a YouTube URL.")
    print()
    print("Choose how to download the audio:")
    print("  1. yt-dlp  — direct download (⚡ fastest, no API key needed, recommended)")
    print("  2. YouTube API  — uses your API key for metadata and lyric alignment (⚙️ slower, requires valid YOUTUBE_API_KEY)")
    print()
    choice = input("Choose 1 or 2 [default: 1]: ").strip() or "1"

    if choice == "1":
        mp3_path = Path(f"songs/{clean_text(args.base_filename)}.mp3")
        if mp3_path.exists():
            print(f"🎵 Reusing existing audio file: {mp3_path}")
            args.input_audio = str(mp3_path)
        else:
            print("▶ Using yt-dlp (no API key required)...")
            os.makedirs("songs", exist_ok=True)
            result = subprocess.run([
                "yt-dlp", "-x", "--audio-format", "mp3",
                "--extractor-args", "youtube:player_client=android",
                "-o", f"songs/{clean_text(args.base_filename)}.%(ext)s",
                args.input_url
            ])
            if result.returncode == 0:
                args.input_audio = str(mp3_path)
                print("✅ Download complete via yt-dlp.")
            else:
                print("❌ yt-dlp failed or was interrupted.")
                print("💡 Tip: re-run this command and select option 2 (YouTube API) as a fallback.")
                sys.exit(1)

    elif choice == "2":
        print("⚠️  YouTube API mode not yet implemented for direct URL download.")
        print("💡  Please use yt-dlp (option 1) for now.")
        sys.exit(1)

    else:
        print("⚠️ Invalid choice. Aborting.")
        sys.exit(1)

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
    choice = input("Choose 1 or 2 [default: 1]: ").strip() or "1"
    if choice == "1":
        args.input_lyrics_timestamps = None
    elif choice == "2":
        args.input_lyrics_text = None
    else:
        error("Invalid choice. Please re-run and choose 1 or 2.")

if args.input_lyrics_text:
    lyrics_path = Path(clean_text(args.input_lyrics_text))
    if not lyrics_path.exists():
        print(f"❌ Lyrics text file not found: {lyrics_path}")
        os.makedirs(lyrics_path.parent, exist_ok=True)
        print("💡 Created empty lyrics file for you.")
        lyrics_path.touch()
        print(f"📝 Open {lyrics_path} in your editor, paste lyrics, save, and press [Enter] once done.")
        while True:
            input("⏸️  Waiting... Press [Enter] after saving your lyrics: ")
            if lyrics_path.exists() and lyrics_path.stat().st_size > 0:
                print("✅ Lyrics file detected and not empty.")
                break
            else:
                print("⚠️  File still missing or empty. Please edit and save it, then press [Enter] again.")

# ───────────────────────────────────────────────────────────────
# Vocals
# ───────────────────────────────────────────────────────────────
if args.vocals_percent is None:
    print()
    print("🎚️  No --vocals-percent specified.")
    print("    1. Full vocals (100%)")
    print("    2. No vocals (0%)")
    choice = input("Choose 1 or 2 [default: 1]: ").strip() or "1"
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
# Run Karaoke generator
# ───────────────────────────────────────────────────────────────
if args.input_lyrics_text and not args.input_lyrics_timestamps:
    print("🎤 Launching interactive lyric timing mode...")

    artist = clean_text(input("Enter artist name: ").strip() or "Unknown Artist")
    title = clean_text(input("Enter song title: ").strip() or Path(args.base_filename).stem)

    lyrics_dir = Path("lyrics")
    lyrics_dir.mkdir(exist_ok=True)
    lyrics_path = lyrics_dir / f"{artist.replace(' ', '_')}_{title.replace(' ', '_')}.txt"

    if not lyrics_path.exists():
        print(f"❌ Lyrics file not found: {lyrics_path}")
        print("💡 Created empty lyrics file for you.")
        lyrics_path.touch()
        print(f"📝 Open {lyrics_path} in your editor, paste your lyrics, save, and press [Enter] once done.")
        while True:
            input("⏸️  Waiting... Press [Enter] after saving your lyrics: ")
            if lyrics_path.exists() and lyrics_path.stat().st_size > 0:
                print("✅ Lyrics file detected and not empty.")
                break
            else:
                print("⚠️  File still missing or empty. Please edit and save it, then press [Enter] again.")

    print("🎬 Starting lyric tapper...")
    try:
        subprocess.run([
            "python3", "scripts/karaoke_auto_sync_lyrics.py",
            "--artist", artist,
            "--title", title,
            "--vocals-percent", str(args.vocals_percent),
            "--interactive"
        ], check=True)

        csv_path = Path("lyrics") / f"{artist.replace(' ', '_')}_{title.replace(' ', '_')}_synced.csv"
        if csv_path.exists():
            print(f"🎬 Rendering video from {csv_path} ...")
            subprocess.run([
                "python3", "scripts/karaoke_core.py",
                "--csv", str(csv_path),
                "--mp3", args.input_audio,
                "--font-size", "140",
                "--offset", str(args.offset)
                ], check=True)

        else:
            print("⚠️  No CSV file generated; skipping render.")

    except subprocess.CalledProcessError as e:
        error(f"Lyric sync failed: {e}")

elif args.input_lyrics_timestamps:
    print("🎬 Rendering final video...")

    timestamp_path = Path(args.input_lyrics_timestamps)

    # Force everything to CSV
    if timestamp_path.suffix.lower() != ".csv":
        csv_candidate = timestamp_path.with_suffix(".csv")
        if csv_candidate.exists():
            print(f"💡 Using corresponding CSV: {csv_candidate}")
            timestamp_path = csv_candidate
        else:
            # Fallback: search for latest *_synced.csv
            lyrics_dir = Path("lyrics")
            csv_files = sorted(lyrics_dir.glob("*_synced.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
            if csv_files:
                latest_csv = csv_files[0]
                print(f"💡 Found latest synced CSV: {latest_csv}")
                timestamp_path = latest_csv
            else:
                error("❌ No valid CSV lyric timing files found. Please run interactive mode first.")

    # Confirm target
    print(f"🎯 Using lyric timing file: {timestamp_path}")

    subprocess.run([
        "python3", "scripts/karaoke_core.py",
        "--csv", str(timestamp_path),
        "--mp3", args.input_audio,
        "--font-size", "140",
        "--offset", str(args.offset)
    ], check=True)

print("\n✅ Karaoke Time completed successfully.")
print("🎬 Output video saved as:", args.output_video)

# end of karaoke_time_cli.py
