#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_generator.py — simplified entrypoint for Karaoke Time
Manual lyrics → interactive tap timing → render
"""

import argparse, os, sys, subprocess, re
from pathlib import Path

def run(cmd: str):
    print(f"\n▶️ {cmd}")
    subprocess.run(cmd, shell=True, check=True)

def sanitize_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s.strip().replace(" ", "_"))

def maybe_trim_audio(mp3_path: Path, max_seconds: float) -> Path:
    if not max_seconds or max_seconds <= 0:
        return mp3_path
    trimmed = mp3_path.with_name(f"{mp3_path.stem}_preview.mp3")
    if trimmed.exists():
        return trimmed
    print(f"✂️  Trimming to first {max_seconds:.1f}s…")
    run(f'ffmpeg -y -i "{mp3_path}" -t {max_seconds} -c copy "{trimmed}"')
    return trimmed

def main():
    parser = argparse.ArgumentParser(description="🎤 Karaoke Time — manual lyrics interactive mode")
    parser.add_argument("input", help="YouTube URL or local MP3 path")
    parser.add_argument("--artist", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--manual-lyrics", action="store_true")
    parser.add_argument("--run-all", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument("--offset", type=float, default=0.0)
    args = parser.parse_args()

    artist_slug = sanitize_name(args.artist)
    title_slug = sanitize_name(args.title)
    lyrics_dir = Path("lyrics"); lyrics_dir.mkdir(exist_ok=True)
    lyrics_path = lyrics_dir / f"{artist_slug}_{title_slug}.txt"
    synced_csv = lyrics_dir / f"{artist_slug}_{title_slug}_synced.csv"

    # 🎧 Audio
    mp3_path = Path(f"{title_slug}.mp3")
    if args.input.startswith("http"):
        if not mp3_path.exists():
            print("🎧 Downloading from YouTube…")
            run(f'yt-dlp -x --audio-format mp3 -o "{mp3_path}" "{args.input}"')
    elif Path(args.input).exists():
        mp3_path = Path(args.input)
    else:
        print("❌ No valid audio input.")
        sys.exit(1)
    mp3_used = maybe_trim_audio(mp3_path, args.max_seconds)

    # 📝 Manual lyrics required
    if not args.manual_lyrics or not lyrics_path.exists():
        print(f"❌ Missing manual lyrics file: {lyrics_path}")
        sys.exit(1)
    print(f"📝 Using manual lyrics from {lyrics_path}")

    # 🎤 Tap-timing
    print("🎵 Starting interactive timing…")
    run(f'python3 scripts/karaoke_auto_sync_lyrics.py '
        f'--artist "{args.artist}" --title "{args.title}" --interactive')

    # 🎬 Render
    if args.run_all:
        print("🎬 Rendering karaoke video…")
        run(f'python3 scripts/karaoke_core.py '
            f'--csv "{synced_csv}" '
            f'--mp3 "{mp3_used}" '
            f'--font-size 140 '
            f'--offset {args.offset}')

    print("\n✅ Done! Karaoke video ready.")

if __name__ == "__main__":
    main()

# end of karaoke_generator.py
