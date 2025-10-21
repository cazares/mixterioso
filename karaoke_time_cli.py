#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_time_cli.py — Unified command-line wrapper for Karaoke Time
Author: Miguel Cázares

Handles audio input, lyrics/timing files, caching, and output setup.
No automatic behavior beyond clear prompts and explicit exits.
"""

import argparse
import sys
from pathlib import Path


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
parser.add_argument("--input-lyrics-text", help="Path to input lyrics text file (.txt)")
parser.add_argument("--input-lyrics-timestamps", help="Path to input lyrics timings file (.csv or .ass)")

parser.add_argument("--output-video", help="Path to output karaoke video file (.mp4)", required=False)

parser.add_argument("--vocals-percent", type=float, help="Vocal mix percentage (0–100)")
parser.add_argument("--no-cache", action="store_true", help="Force regeneration of Demucs stems")

args = parser.parse_args()


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
# Validation
# ───────────────────────────────────────────────────────────────
# Required: input audio + output video
if not args.input_audio or not args.output_video:
    error("You must specify both --input-audio and --output-video, "
          "or provide --base-filename to auto-fill them.")

# At least one of lyrics-text or lyrics-timestamps
if not args.input_lyrics_text and not args.input_lyrics_timestamps:
    error("You must specify either --input-lyrics-text (for interactive mode) "
          "or --input-lyrics-timestamps (for non-interactive mode).")

# If both provided, ask user which to use
if args.input_lyrics_text and args.input_lyrics_timestamps:
    print("⚠️  Both --input-lyrics-text and --input-lyrics-timestamps were provided.")
    print("    1. Use interactive mode (generate new CSV from text)")
    print("    2. Use existing timings (non-interactive)")
    choice = input("Choose 1 or 2: ").strip()
    if choice == "1":
        info("Using lyrics text (interactive mode).")
        args.input_lyrics_timestamps = None
    elif choice == "2":
        info("Using existing timings (non-interactive mode).")
        args.input_lyrics_text = None
    else:
        error("Invalid choice. Please re-run and choose 1 or 2.")

# Validate files
audio_path = Path(args.input_audio)
if not audio_path.exists():
    error(f"Input audio file not found: {audio_path}")

if args.input_lyrics_text and not Path(args.input_lyrics_text).exists():
    error(f"Lyrics text file not found: {args.input_lyrics_text}")

if args.input_lyrics_timestamps and not Path(args.input_lyrics_timestamps).exists():
    error(f"Timings file not found: {args.input_lyrics_timestamps}")

# Validate vocals percentage
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

# Sanity check for numeric range
if not (0.0 <= args.vocals_percent <= 100.0):
    error("--vocals-percent must be between 0 and 100.")


# ───────────────────────────────────────────────────────────────
# Summary
# ───────────────────────────────────────────────────────────────
print("\n🎬 Karaoke Time job summary:")
print(f"   Input audio:          {args.input_audio}")
print(f"   Lyrics text:          {args.input_lyrics_text or '(none)'}")
print(f"   Lyrics timestamps:    {args.input_lyrics_timestamps or '(none)'}")
print(f"   Output video:         {args.output_video}")
print(f"   Vocals percent:       {args.vocals_percent}%")
print(f"   Force no-cache:       {'Yes' if args.no_cache else 'No'}")
print()

# ───────────────────────────────────────────────────────────────
# Exit placeholder (no automatic processing)
# ───────────────────────────────────────────────────────────────
print("✅ Validation complete. All required inputs are present.")
print("ℹ️  Next step: connect to your existing scripts:")
print("    - run_demucs_if_needed()")
print("    - run_interactive_mode() or run_noninteractive_mode()")
print("    - render_karaoke_video()")
sys.exit(0)
