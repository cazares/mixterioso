#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps.py
───────────────────────
Downloads audio (YouTube or local file), converts to MP3,
then uses local OpenAI Whisper to create timestamp CSVs
with "sample_lyric_N" placeholders.

No API key required. All filenames use underscores.
"""

import os, sys, csv, time, logging, subprocess
from pathlib import Path
from dotenv import load_dotenv
import whisper  # local model

# ----- setup -----
load_dotenv()
SONGS_DIR = Path("songs")
LYRICS_DIR = Path("lyrics")
SONGS_DIR.mkdir(exist_ok=True)
LYRICS_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ----- helpers -----
def is_youtube_url(s: str) -> bool:
    return ("youtube.com" in s) or ("youtu.be" in s)

def sanitize_filename(name: str) -> str:
    """Replace spaces with underscores and strip illegal chars."""
    return name.replace(" ", "_").replace("/", "_")

def download_audio(url: str) -> Path:
    """Download YouTube audio via yt-dlp and return MP3 path with underscores."""
    logging.info(f"🎥 Downloading from YouTube: {url}")
    output_path = SONGS_DIR / "%(title)s.%(ext)s"
    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "--no-playlist",
        "-o", str(output_path),
        url,
    ]
    try:
        subprocess.run(cmd, check=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"yt-dlp failed: {e}")
        sys.exit(2)

    mp3_files = sorted(SONGS_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3_files:
        logging.error("No MP3 found after download.")
        sys.exit(3)
    latest = mp3_files[0]
    fixed_name = sanitize_filename(latest.stem) + ".mp3"
    fixed_path = latest.with_name(fixed_name)
    if latest != fixed_path:
        latest.rename(fixed_path)
    logging.info(f"✅ Download complete → {fixed_path.name}")
    return fixed_path

def whisper_segments(audio_path: Path):
    """Run local Whisper transcription."""
    logging.info(f"🎧 Transcribing locally: {audio_path}")
    try:
        model = whisper.load_model("small")  # choose tiny, small, base, medium, large
        result = model.transcribe(str(audio_path), verbose=False)
        segments = result.get("segments", [])
        logging.info(f"Received {len(segments)} segments from Whisper (local)")
        return segments
    except Exception as e:
        logging.error(f"Local Whisper transcription failed: {e}")
        return []

def make_csv_from_segments(segments, out_path):
    """Write [start,end,sample_lyric_N] CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, seg in enumerate(segments):
            start = round(seg.get("start", 0), 2)
            end = round(seg.get("end", 0), 2)
            writer.writerow([start, end, f"sample_lyric_{i+1}"])
    logging.info(f"✅ CSV written: {out_path}")

# ----- main -----
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_timestamps.py <audiofile.mp3 | youtube_url>")
        sys.exit(1)

    source = sys.argv[1]
    if is_youtube_url(source):
        audio_path = download_audio(source)
    else:
        audio_path = Path(source)
        if not audio_path.exists():
            logging.error(f"Audio file not found: {audio_path}")
            sys.exit(2)

    csv_name = sanitize_filename(audio_path.stem) + "_timestamps.csv"
    out = LYRICS_DIR / csv_name

    t0 = time.time()
    segs = whisper_segments(audio_path)
    if not segs:
        logging.error("No segments returned; exiting")
        sys.exit(3)
    make_csv_from_segments(segs, out)
    logging.info(f"Done in {time.time() - t0:.1f}s")

if __name__ == "__main__":
    main()
# end of generate_timestamps.py
