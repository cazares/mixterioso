#!/usr/bin/env python3
"""
scripts/3_auto_timing_whisperx.py

Forced-align lyrics to audio using WhisperX:
- Loads audio (mp3/wav)
- Loads ground-truth lyrics from txt
- Transcribes OR uses lyrics directly
- Runs WhisperX's Wav2Vec2-based forced aligner
- Generates line-level start/end timings
- Emits timings/<slug>.csv (line_index,start,end,text)
"""

import argparse
import csv
import sys
from pathlib import Path
from typing import List, Tuple

import torch
import whisperx

BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------
# Helpers
# -------------------------------

def read_lyrics_lines(txt_path: Path) -> List[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return lines

def choose_audio(slug: str, override: Path = None) -> Path:
    if override and override.exists():
        return override
    # Prefer full mix from mixes/<slug>_*.wav
    mix_dir = BASE_DIR / "mixes"
    candidates = list(mix_dir.glob(f"{slug}_*.wav"))
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    # fallback mp3
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        return mp3
    print(f"[ERR] No audio found for slug={slug}", file=sys.stderr)
    sys.exit(1)

def write_csv(slug: str, rows: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, s, e, t in rows:
            w.writerow([li, f"{s:.3f}", f"{e:.3f}", t])
    print(f"[OK] wrote {out} ({len(rows)} lines)")
    return out

# -------------------------------
# Alignment core
# -------------------------------

def align_lines_with_whisperx(
    audio_path: Path,
    lines: List[str],
    lang: str = "en"
) -> List[Tuple[int, float, float, str]]:
    """
    Forced align the entire text with WhisperX,
    then map word timings -> line timings.
    """
    full_text = " ".join(lines)

    device = "cuda" if torch.cuda.is_available() else "cpu"

    print("[INFO] Loading WhisperX ASR model (CPU-safe compute type)...")
    asr_model = whisperx.load_model(
        "small.en",
        device=device,
        compute_type="int8"   # <<< FIX HERE
    )

    print("[INFO] Running ASR transcription (rough pass)...")
    asr_result = asr_model.transcribe(str(audio_path))

    print("[INFO] Loading alignment model...")
    alignment_model, metadata = whisperx.load_align_model(
        language_code=lang,
        device=device,
        compute_type="int8"   # <<< OPTIONAL but safe for CPU
    )

    print("[INFO] Running forced alignment...")
    aligned = whisperx.align(
        transcript=asr_result["segments"],
        model=alignment_model,
        align_model_metadata=metadata,
        audio_path=str(audio_path),
        device=device,
    )

# -------------------------------
# CLI
# -------------------------------

def main():
    ap = argparse.ArgumentParser(description="Forced-align lyrics using WhisperX")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--mp3", type=str, default=None)
    ap.add_argument("--txt", type=str, default=None)
    ap.add_argument("--lang", type=str, default="en")
    args = ap.parse_args()

    slug = args.slug
    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    audio_path = choose_audio(slug, Path(args.mp3) if args.mp3 else None)

    if not txt_path.exists():
        print(f"[ERR] missing txt: {txt_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] slug={slug}")
    print(f"[INFO] txt={txt_path}")
    print(f"[INFO] audio={audio_path}")

    lines = read_lyrics_lines(txt_path)
    print(f"[INFO] loaded {len(lines)} lyric lines")

    rows = align_lines_with_whisperx(
        audio_path=audio_path,
        lines=lines,
        lang=args.lang,
    )

    write_csv(slug, rows)

if __name__ == "__main__":
    main()

# end of 3_auto_timing_whisperx.py
