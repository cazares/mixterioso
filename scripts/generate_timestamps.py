#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps.py
Generates karaoke_time_by_miguel.py-compatible CSV timing data from audio.
Steps:
  1) Optional YouTube download (via yt-dlp)
  2) Local Whisper transcription
  3) Merge Whisper start times with lyric lines from .txt
  4) Output CSV with headers: line,start
"""

import csv
import logging
import subprocess
from pathlib import Path
import whisper
import sys

# ------------------------- Directories ------------------------- #
ROOT_DIR = Path(__file__).resolve().parent.parent
SONGS_DIR = ROOT_DIR / "songs"
LYRICS_DIR = ROOT_DIR / "lyrics"
SONGS_DIR.mkdir(exist_ok=True)
LYRICS_DIR.mkdir(exist_ok=True)

# ------------------------- Logging Setup ----------------------- #
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

# ------------------------- Helpers ----------------------------- #
def sanitize_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name).strip("_")

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
    subprocess.run(cmd, check=True, text=True)
    mp3_files = sorted(SONGS_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
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
        model = whisper.load_model("small")
        result = model.transcribe(str(audio_path), verbose=False)
        # Return the list of segments directly
        return result.get("segments", [])
    except Exception as e:
        logging.error(f"Local Whisper transcription failed: {e}")
        return []

def write_karaoke_csv(csv_path: Path, txt_path: Path, segments: list):
    """Write CSV compatible with karaoke_time_by_miguel.py (headers: line,start)."""
    import csv, logging

    if not txt_path.exists():
        logging.error(f"Lyrics file not found: {txt_path}")
        return

    # Load text lines
    with open(txt_path, "r", encoding="utf-8") as f:
        lyric_lines = [line.strip() for line in f if line.strip()]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        if len(lyric_lines) != len(segments):
            logging.warning(
                f"CAUTION: lyrics count ({len(lyric_lines)}) "
                f"!= segment count ({len(segments)})."
            )
        for i, seg in enumerate(segments):
            start = float(seg.get("start", 0))
            text = lyric_lines[i] if i < len(lyric_lines) else f"sample_line_{i+1}"
            writer.writerow([text, f"{start:.3f}"])
    logging.info(f"✅ Wrote karaoke CSV → {csv_path}")

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

# ------------------------- Main CLI ---------------------------- #
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate timestamp CSVs for karaoke_time_by_miguel.py")
    ap.add_argument("--audio", help="Path to .mp3 or YouTube URL", required=True)
    ap.add_argument("--text", help="Path to .txt lyrics file", required=True)
    args = ap.parse_args()

    audio_path = Path(args.audio)
    if str(audio_path).startswith(("http://", "https://")):
        audio_path = download_audio(args.audio)

    csv_name = sanitize_filename(audio_path.stem) + "_timestamps.csv"
    csv_path = LYRICS_DIR / csv_name

    segments = whisper_segments(audio_path)
    write_karaoke_csv(csv_path, txt_path, result["segments"])

    logging.info(f"✅ Done. CSV saved at {csv_path}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error: {e}")
        sys.exit(1)

# end of generate_timestamps.py
