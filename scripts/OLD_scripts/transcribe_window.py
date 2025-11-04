#!/usr/bin/env python3
"""
transcribe_window.py
Cut a window from an audio file and run Whisper on JUST that window.

Usage:
  python3 scripts/transcribe_window.py \
    --audio songs/foo_mono.mp3 \
    --start 0 \
    --end 30 \
    --language es

Output lines look like:
  26.50,28.90,Me dice que me ama
(absolute times in the ORIGINAL audio)
"""

import argparse
import os
import subprocess
import tempfile

import whisper  # make sure this is installed in this env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="audio file (mp3/wav)")
    ap.add_argument("--start", type=float, required=True, help="start (sec)")
    ap.add_argument("--end", type=float, required=True, help="end (sec)")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--language", default="es")
    args = ap.parse_args()

    dur = max(0.1, args.end - args.start)

    with tempfile.TemporaryDirectory() as td:
        seg_path = os.path.join(td, "seg.wav")
        # hard cut the exact window
        subprocess.check_call(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(args.start),
                "-t",
                str(dur),
                "-i",
                args.audio,
                "-ac",
                "1",
                "-ar",
                "16000",
                seg_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        model = whisper.load_model(args.model)
        out = model.transcribe(
            seg_path,
            language=args.language,
            temperature=0.0,
            condition_on_previous_text=False,
        )

        for segm in out.get("segments", []):
            text = segm.get("text", "").strip()
            if not text:
                continue
            abs_start = args.start + segm["start"]
            abs_end = args.start + segm["end"]
            print(f"{abs_start:.2f},{abs_end:.2f},{text}")


if __name__ == "__main__":
    main()
# end of transcribe_window.py
