#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_only_from_txt.py — faster-whisper (English only, no VAD)
Generates Karaoke-Time-compatible CSV timing data from known lyrics.
Runs fully inside demucs_env.
"""

import csv, logging, sys
from pathlib import Path
from faster_whisper import WhisperModel

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

def generate_timing(audio_path: Path, txt_path: Path, output_csv: Path):
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        logging.error(f"❌ Lyrics file is empty: {txt_path}")
        sys.exit(1)

    logging.info("🎧 Loading faster-whisper (base, CPU float32, English only)...")
    model = WhisperModel("base", device="cpu", compute_type="float32")

    logging.info("🕒 Extracting approximate timestamps (no VAD, English forced)...")
    segments, _ = model.transcribe(
        str(audio_path),
        beam_size=1,
        best_of=1,
        vad_filter=False,       # ✅ no voice activity detection
        language="en",          # ✅ force English
        without_timestamps=False,
    )

    segs = list(segments)
    if not segs:
        logging.error("💀 No segments detected. Aborting.")
        sys.exit(1)

    logging.info(f"✅ Detected {len(segs)} audio segments. Mapping to {len(lines)} lyric lines...")

    mapped = []
    for i, line in enumerate(lines):
        if i < len(segs):
            start = segs[i].start
        else:
            start = segs[-1].end + 2.0
        mapped.append([line, f"{start:.3f}"])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        writer.writerows(mapped)

    logging.info(f"✅ Wrote {len(mapped)} lines → {output_csv}")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Align-only using faster-whisper (English only, no VAD).")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    txt_path = Path(args.text).expanduser().resolve()
    output_csv = Path(args.output).expanduser().resolve()

    if not audio_path.exists():
        logging.error(f"Audio not found: {audio_path}")
        sys.exit(1)
    if not txt_path.exists():
        logging.error(f"Lyrics not found: {txt_path}")
        sys.exit(1)

    generate_timing(audio_path, txt_path, output_csv)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)

# end of align_only_from_txt.py
