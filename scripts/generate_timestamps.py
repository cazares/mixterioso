#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps_whisper.py
──────────────────────────────
Uses OpenAI Whisper to produce timestamp skeletons from audio,
filling lyric text placeholders ("sample lyric N").

Now:
- Loads .env for OPENAI_API_KEY and YOUTUBE_API_KEY
- Warns if keys missing
- Logs each Whisper segment and timing
"""

import os, sys, csv, time, logging
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

# ----- functions -----
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
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, seg in enumerate(segments):
            start = round(seg.get("start", 0), 2)
            end = round(seg.get("end", 0), 2)
            writer.writerow([start, end, f"sample lyric {i+1}"])
    logging.info(f"✅ CSV written: {out_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_timestamps_whisper.py audiofile.mp3")
        sys.exit(1)
    audio = Path(sys.argv[1])
    if not audio.exists():
        logging.error(f"Audio file not found: {audio}")
        sys.exit(2)
    out = Path("lyrics") / f"{audio.stem}_timestamps.csv"

    t0 = time.time()
    segs = whisper_segments(str(audio))
    if not segs:
        logging.error("No segments returned; exiting")
        sys.exit(3)
    make_csv_from_segments(segs, out)
    logging.info(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
# end of generate_timestamps_whisper.py
