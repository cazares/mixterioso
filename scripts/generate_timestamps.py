#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps_whisper.py
──────────────────────────────
Creates timestamp CSVs ("sample lyric N") using OpenAI Whisper.
If given a YouTube URL, automatically downloads and converts it to MP3
for transcription.

Dependencies:
  pip3 install openai python-dotenv yt-dlp
"""

import os, sys, csv, time, logging, subprocess
from pathlib import Path
from dotenv import load_dotenv
import openai

# ----- setup -----
load_dotenv()  # read .env if present
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
YOUTUBE_KEY = os.getenv("YOUTUBE_API_KEY")

if not OPENAI_KEY:
    logging.warning("⚠️  OPENAI_API_KEY not found in .env or environment")
else:
    openai.api_key = OPENAI_KEY
if not YOUTUBE_KEY:
    logging.debug("No YOUTUBE_API_KEY found (not required for Whisper)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

SONGS_DIR = Path("songs")
LYRICS_DIR = Path("lyrics")
SONGS_DIR.mkdir(exist_ok=True)
LYRICS_DIR.mkdir(exist_ok=True)

# ----- helpers -----
def is_youtube_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://") and "youtube.com" in s or "youtu.be" in s

def download_audio(url: str) -> Path:
    """Download YouTube audio via yt-dlp to songs/ and return mp3 path."""
    logging.info(f"🎥 Downloading from YouTube: {url}")
    output_path = SONGS_DIR / "%(title)s.%(ext)s"
    cmd = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "--no-playlist",
        "-o", str(output_path),
        url
    ]
    try:
        subprocess.run(cmd, check=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"yt-dlp failed: {e}")
        sys.exit(2)

    # Find newest mp3 in songs/
    mp3_files = sorted(SONGS_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not mp3_files:
        logging.error("No MP3 found after download.")
        sys.exit(3)
    latest = mp3_files[0]
    logging.info(f"✅ Download complete → {latest.name}")
    return latest

def whisper_segments(audio_path):
    """Call OpenAI Whisper to get verbose JSON segment timestamps."""
    logging.info(f"🎧 Transcribing (timestamps only): {audio_path}")
    try:
        with open(audio_path, "rb") as f:
            resp = openai.Audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json"
            )
        segs = resp.segments
        logging.info(f"Received {len(segs)} segments from Whisper")
        for i, s in enumerate(segs[:5]):  # show first few
            logging.debug(f"Segment {i+1}: {s['start']:.2f}-{s['end']:.2f}s")
        return segs
    except Exception as e:
        logging.error(f"Whisper transcription failed: {e}")
        return []

def make_csv_from_segments(segments, out_path):
    """Write [start,end,sample lyric N] CSV from Whisper segments."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, seg in enumerate(segments):
            start = round(seg.get("start", 0), 2)
            end = round(seg.get("end", 0), 2)
            writer.writerow([start, end, f"sample lyric {i+1}"])
    logging.info(f"✅ CSV written: {out_path}")

# ----- main -----
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_timestamps_whisper.py <audiofile.mp3 | youtube_url>")
        sys.exit(1)

    source = sys.argv[1]
    if is_youtube_url(source):
        audio_path = download_audio(source)
    else:
        audio_path = Path(source)
        if not audio_path.exists():
            logging.error(f"Audio file not found: {audio_path}")
            sys.exit(2)

    out = LYRICS_DIR / f"{audio_path.stem}_timestamps.csv"
    t0 = time.time()
    segs = whisper_segments(str(audio_path))
    if not segs:
        logging.error("No segments returned; exiting")
        sys.exit(3)
    make_csv_from_segments(segs, out)
    logging.info(f"Done in {time.time() - t0:.1f}s")

if __name__ == "__main__":
    main()
# end of generate_timestamps_whisper.py
