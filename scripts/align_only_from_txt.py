#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_only_from_txt.py — hybrid aligner
Prefers Whisper JSON alignment, falls back to Faster-Whisper if JSON missing.
"""

import csv, json, logging, sys
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

def align_from_json(json_path: Path, txt_path: Path, output_csv: Path):
    raw = json.loads(json_path.read_text(encoding="utf-8"))

    # handle both formats: list or dict
    if isinstance(raw, list):
        segs = raw
    elif isinstance(raw, dict):
        segs = raw.get("segments", [])
    else:
        logging.error("❌ Unrecognized JSON structure.")
        sys.exit(1)

    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not segs or not lines:
        logging.error("❌ Missing segments or lyrics")
        sys.exit(1)

    mapped = []
    n_txt, n_json = len(lines), len(segs)
    for i, line in enumerate(lines):
        j = round(i / max(1, n_txt - 1) * (n_json - 1))
        seg = segs[j]
        # each segment may have start/end/time keys depending on model
        start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0)
        mapped.append([line, f"{float(start):.3f}"])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        writer.writerows(mapped)

    logging.info(
        f"✅ Alignment complete:\n"
        f"  • {len(lines)} lyric lines from TXT (source of truth)\n"
        f"  • {len(segs)} segments from Whisper JSON\n"
        f"  → Both merged into {output_csv.name}"
    )
    
def align_from_faster_whisper(audio_path: Path, txt_path: Path, output_csv: Path):
    from faster_whisper import WhisperModel

    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        logging.error(f"❌ Lyrics file is empty: {txt_path}")
        sys.exit(1)

    logging.info("🎧 Loading faster-whisper (base, CPU float32, English only)...")
    model = WhisperModel("base", device="cpu", compute_type="float32")

    logging.info("🕒 Transcribing for approximate timestamps...")
    segments, _ = model.transcribe(
        str(audio_path),
        beam_size=1,
        best_of=1,
        vad_filter=False,
        language="en",
        without_timestamps=False,
    )
    segs = list(segments)
    if not segs:
        logging.error("💀 No segments detected. Aborting.")
        sys.exit(1)

    mapped = []
    for i, line in enumerate(lines):
        start = segs[i].start if i < len(segs) else segs[-1].end + 2.0
        mapped.append([line, f"{start:.3f}"])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        writer.writerows(mapped)

    logging.info(f"✅ Wrote {len(mapped)} lines → {output_csv}")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Align lyrics using Whisper JSON or fallback Faster-Whisper.")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--json", help="Optional preexisting Whisper JSON file")
    args = ap.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    txt_path = Path(args.text).expanduser().resolve()
    output_csv = Path(args.output).expanduser().resolve()
    json_path = Path(args.json).expanduser().resolve() if args.json else None

    if json_path and json_path.exists():
        align_from_json(json_path, txt_path, output_csv)
    else:
        align_from_faster_whisper(audio_path, txt_path, output_csv)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)

# end of align_only_from_txt.py
