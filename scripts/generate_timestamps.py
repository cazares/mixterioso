#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps_whisper.py — uses OpenAI Whisper to generate
timestamp skeletons from audio, filling text with sample placeholders.
"""

import sys, csv, logging, time
from pathlib import Path
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

def whisper_segments(audio_path):
    logging.info(f"🎧 Transcribing (timestamps only): {audio_path}")
    with open(audio_path, "rb") as f:
        resp = openai.Audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json"
        )
    return resp.segments

def make_csv_from_segments(segments, out_path):
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, seg in enumerate(segments):
            start = round(seg["start"], 2)
            end = round(seg["end"], 2)
            writer.writerow([start, end, f"sample lyric {i+1}"])
    logging.info(f"✅ wrote {out_path}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/generate_timestamps_whisper.py audiofile.mp3")
        sys.exit(1)
    audio = Path(sys.argv[1])
    out = Path("lyrics") / f"{audio.stem}_timestamps.csv"
    t0 = time.time()
    segs = whisper_segments(str(audio))
    make_csv_from_segments(segs, out)
    logging.info(f"done in {time.time()-t0:.1f}s")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
