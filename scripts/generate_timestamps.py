#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps.py
Generates karaoke_time_by_miguel.py-compatible CSV timing data from audio.
Steps:
  1) Optional YouTube download (via yt-dlp)
  2) Local Whisper transcription (only if CSV missing)
  3) Merge Whisper start times with lyric lines from .txt
  4) Output CSV with headers: line,start
  5) If CSV already exists, validate and auto-fix header names if needed
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
        return result.get("segments", [])
    except Exception as e:
        logging.error(f"Local Whisper transcription failed: {e}")
        return []

def write_karaoke_csv(csv_path: Path, txt_path: Path, segments: list):
    """Write CSV compatible with karaoke_time_by_miguel.py (headers: line,start)."""
    if not txt_path.exists():
        logging.error(f"Lyrics file not found: {txt_path}")
        return

    with open(txt_path, "r", encoding="utf-8") as f:
        lyric_lines = [line.strip() for line in f if line.strip()]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        if len(lyric_lines) != len(segments):
            logging.warning(
                f"CAUTION: lyrics count ({len(lyric_lines)}) != segment count ({len(segments)})"
            )
        for i, seg in enumerate(segments):
            start = float(seg.get("start", 0))
            text = lyric_lines[i] if i < len(lyric_lines) else f"sample_line_{i+1}"
            writer.writerow([text, f"{start:.3f}"])
    logging.info(f"✅ Wrote karaoke CSV → {csv_path}")

def validate_and_fix_csv(csv_path: Path):
    """Ensure CSV has headers ['line', 'start']. Auto-fix if needed."""
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            logging.warning(f"{csv_path.name} is empty.")
            return False

        headers = [h.strip().lower() for h in rows[0]]
        # If first row is not header but numeric, handle as no-header CSV
        first_is_data = all(
            cell.replace(".", "", 1).isdigit() for cell in rows[0][:2]
        )
        if first_is_data:
            logging.info("Detected headerless CSV with numeric first line — auto-repairing.")
            fixed_rows = [["line", "start"]]
            for r in rows:
                if len(r) >= 3:
                    start = r[0]
                    text = r[2]
                elif len(r) == 2:
                    start = r[0]
                    text = r[1]
                else:
                    continue
                fixed_rows.append([text.strip('"'), start])
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fixed_rows)
            logging.info(f"🩹 Fixed headerless CSV → {csv_path.name}")
            return True

        # Correct headers
        if headers == ["line", "start"]:
            logging.info(f"✅ CSV already in correct format: {csv_path.name}")
            return True

        # Convert [start,end,text] or [start,text]
        if set(headers) >= {"start", "end", "text"} or headers[:3] == ["start", "end", "text"]:
            fixed_rows = [["line", "start"]]
            for r in rows[1:]:
                if len(r) >= 3:
                    start = r[0]
                    text = r[2]
                    fixed_rows.append([text, start])
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fixed_rows)
            logging.info(f"🩹 Fixed [start,end,text] CSV → {csv_path.name}")
            return True

        logging.warning(f"❓ Unknown CSV format in {csv_path.name}, manual check needed.")
        return False

    except Exception as e:
        logging.error(f"Error validating CSV: {e}")
        return False

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

    txt_path = Path(args.text)
    csv_name = sanitize_filename(audio_path.stem) + "_timestamps.csv"

    # 👇 Fix naming if it came from a YouTube URL (like watch_v_a9eNQZbjpJk)
    if "watch_v_" in csv_name:
        csv_name = sanitize_filename(txt_path.stem) + "_timestamps.csv"

    csv_path = LYRICS_DIR / csv_name

    # If CSV exists, validate/fix headers and skip whisper
    if csv_path.exists():
        logging.info(f"📂 Existing CSV found → {csv_path.name}")
        ok = validate_and_fix_csv(csv_path)
        if ok:
            logging.info("✅ Skipping Whisper step; CSV is ready.")
            return

    # Else, generate from Whisper
    segments = whisper_segments(audio_path)
    write_karaoke_csv(csv_path, txt_path, segments)
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
