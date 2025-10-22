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
# UTF-8 cleanup helper
# ───────────────────────────────────────────────────────────────
def clean_text(s: str) -> str:
    return (
        s.encode("utf-8", "ignore")
        .decode("utf-8")
        .replace("\uFEFF", "")
        .replace("\uFFFD", "")
        .replace("\xa0", " ")
        .strip()
    )

# ───────────────────────────────────────────────────────────────
# Argument parsing
# ───────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description="🎤 Karaoke Time — friendly CLI for lyric video generation"
)

# 🆕 Added optional artist/title args
parser.add_argument("--artist", help="Artist name (optional, for labeling output)")
parser.add_argument("--title", help="Song title (optional, for labeling output)")

# 🆕 Removed: --base-filename (simplified design)
parser.add_argument("--input-audio", help="Path to input audio file (.mp3 or .wav)")
parser.add_argument("--input-url", help="YouTube URL to download and process")
parser.add_argument("--input-lyrics-text", help="Path to lyrics text file (.txt)")
parser.add_argument("--input-lyrics-timestamps", help="Path to lyrics timings file (.csv)")
parser.add_argument("--output-video", help="Path to output karaoke video file (.mp4)")
parser.add_argument("--vocals-percent", type=float, help="Vocal mix percentage (0–100)")
parser.add_argument("--no-cache", action="store_true", help="Force regeneration of Demucs stems")
parser.add_argument("--offset", type=float, default=0.0, help="Shift lyric timestamps (seconds, can be negative)")
args = parser.parse_args()

# ───────────────────────────────────────────────────────────────
# Load environment
# ───────────────────────────────────────────────────────────────
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# ───────────────────────────────────────────────────────────────
# Audio source (local or URL)
# ───────────────────────────────────────────────────────────────
if args.input_url:
    args.input_url = clean_text(args.input_url)
    parsed = urlparse(args.input_url)
    stem = Path(parsed.path).stem or "downloaded_audio"     # 🆕 safer stem derivation
    mp3_path = Path(f"songs/{stem}.mp3")
    os.makedirs("songs", exist_ok=True)

    if mp3_path.exists():
        print(f"🎵 Reusing existing audio file: {mp3_path}")
        args.input_audio = str(mp3_path)
    else:
        print("▶ Using yt-dlp (no API key required)...")
        result = subprocess.run([
            "yt-dlp", "-x", "--audio-format", "mp3",
            "--extractor-args", "youtube:player_client=android",
            "-o", f"songs/{stem}.%(ext)s",
            args.input_url
        ])
        if result.returncode != 0:
            error("yt-dlp failed to download audio.")
        print("✅ Download complete via yt-dlp.")
        args.input_audio = str(mp3_path)

# ───────────────────────────────────────────────────────────────
# Lyrics handling
# ───────────────────────────────────────────────────────────────
if not args.input_lyrics_text and not args.input_lyrics_timestamps:
    error("You must specify either --input-lyrics-text or --input-lyrics-timestamps.")

# 🆕 Simplified: no artist/title prompts, use provided text file directly
if args.input_lyrics_text and not args.input_lyrics_timestamps:
    lyrics_path = Path(clean_text(args.input_lyrics_text))
    if not lyrics_path.exists():
        error(f"Lyrics text file not found: {lyrics_path}")

        print("🎬 Starting lyric tapper...")
    try:
        subprocess.run([
            "python3", "scripts/karaoke_auto_sync_lyrics.py",
            "--lyrics", str(lyrics_path),
            "--interactive"
        ], check=True)

        csv_path = Path(lyrics_path).with_name(f"{Path(lyrics_path).stem}_synced.csv")
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
            warn("⚠️  No CSV file generated; skipping render.")

    except subprocess.CalledProcessError as e:
        error(f"Lyric sync failed: {e}")

# 🆕 CSV-only rendering (non-interactive mode)
elif args.input_lyrics_timestamps:
    print("🎬 Rendering final video...")

    timestamp_path = Path(args.input_lyrics_timestamps)
    if timestamp_path.suffix.lower() != ".csv":
        csv_candidate = timestamp_path.with_suffix(".csv")
        if csv_candidate.exists():
            print(f"💡 Using corresponding CSV: {csv_candidate}")
            timestamp_path = csv_candidate
        else:
            lyrics_dir = Path("lyrics")
            csv_files = sorted(lyrics_dir.glob("*_synced.csv"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
            if csv_files:
                latest_csv = csv_files[0]
                print(f"💡 Found latest synced CSV: {latest_csv}")
                timestamp_path = latest_csv
            else:
                error("❌ No valid CSV lyric timing files found. Run interactive mode first.")

    print(f"🎯 Using lyric timing file: {timestamp_path}")
    subprocess.run([
        "python3", "scripts/karaoke_core.py",
        "--csv", str(timestamp_path),
        "--mp3", args.input_audio,
        "--font-size", "140",
        "--offset", str(args.offset)
    ], check=True)

# 🆕 Ensure consistent default output folder
output_path = args.output_video or Path("output") / f"{Path(args.input_audio).stem}_karaoke.mp4"
os.makedirs(Path(output_path).parent, exist_ok=True)
print("\n✅ Karaoke Time completed successfully.")
print(f"🎬 Output video saved as: {output_path}")

# end of karaoke_time_cli.py
