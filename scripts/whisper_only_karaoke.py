#!/usr/bin/env python3
# whisper_only_karaoke.py
# Purpose: Transcribe a vocals-only audio file with possible leading silence.
# Pipeline:
#   1) Detect leading silence via ffmpeg silencedetect.
#   2) Create a temp mono 16 kHz WAV starting after that leading silence (internal silences preserved).
#   3) Transcribe with Faster-Whisper (VAD off).
#   4) Group segments into karaoke-like phrases.
#   5) Shift timestamps back by the detected leading silence so all times are from ORIGINAL file start.
# Outputs: <prefix>_phrases.txt, <prefix>_phrases.csv, <prefix>_phrases.json in --out-dir.

import argparse
import csv
import json
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------- Silence detection ----------------

def detect_leading_silence_seconds(
    audio_path: Path, noise_db: float = -38.0, min_silence_sec: float = 0.30
) -> float:
    """
    Returns estimated leading silence in seconds using ffmpeg silencedetect.
    If none found, returns 0.0.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f", "null",
        "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log = proc.stderr  # silencedetect logs to stderr

    saw_start_zero = False
    for line in log.splitlines():
        line = line.strip()
        if line.startswith("silence_start:"):
            try:
                val = float(line.split("silence_start:")[1].strip())
                if abs(val) < 1e-3:  # leading silence at t≈0
                    saw_start_zero = True
            except Exception:
                pass
        if saw_start_zero and line.startswith("silence_end:"):
            # Format: "silence_end: 10.9441 | silence_duration: 10.9441"
            try:
                parts = line.split("silence_end:")[1].strip().split("|")[0].strip()
                return float(parts)
            except Exception:
                break
    return 0.0

def make_trimmed_for_whisper(src: Path, lead_s: float) -> Path:
    """
    Create a temp WAV starting at lead_s. Internal silences untouched.
    Output is mono 16 kHz for fastest inference.
    """
    tmp = Path(tempfile.gettempdir()) / f"{src.stem}_mono16k_trim.wav"
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{lead_s:.3f}",
        "-i", str(src),
        "-ac", "1", "-ar", "16000",
        str(tmp)
    ]
    subprocess.run(cmd, check=True)
    return tmp

# ---------------- Text utils + grouping ----------------

def _clean_text(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\s*\[.*?\]\s*", " ", t)   # [Music], [Applause], etc.
    t = re.sub(r"\s*<.*?>\s*", " ", t)     # <unk> tags
    t = t.replace("♪", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def group_segments(
    segs: List[Dict[str, Any]],
    gap_threshold: float = 0.8,
    max_duration: float = 7.0,
    min_duration_for_punct_break: float = 2.0,
    max_chars: int = 120,
) -> List[Dict[str, Any]]:
    """
    Group Whisper segments into karaoke-friendly phrases.
    Break on: large gap, max duration, max chars, or end punctuation after a few seconds.
    """
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    prev_end: Optional[float] = None
    end_punct_re = re.compile(r"[.!?…]\s*$")

    for s in segs:
        start = float(s["start"])
        end = float(s["end"])
        text = _clean_text(str(s["text"]))
        if not text:
            prev_end = end
            continue

        if current is None:
            current = {"start": start, "end": end, "texts": [text]}
            prev_end = end
            continue

        gap = 0.0 if prev_end is None else max(0.0, start - prev_end)
        duration_if_added = end - float(current["start"])
        chars_if_added = sum(len(t) for t in current["texts"]) + len(text)
        end_punct = bool(end_punct_re.search(text))

        should_break = (
            gap >= gap_threshold
            or duration_if_added > max_duration
            or chars_if_added > max_chars
            or (end_punct and duration_if_added >= min_duration_for_punct_break)
        )

        if should_break:
            grouped_text = _clean_text(" ".join(current["texts"]))
            if grouped_text:
                groups.append({
                    "start": round(float(current["start"]), 3),
                    "end": round(float(current["end"]), 3),
                    "duration": round(float(current["end"]) - float(current["start"]), 3),
                    "text": grouped_text,
                })
            current = {"start": start, "end": end, "texts": [text]}
        else:
            current["end"] = end
            current["texts"].append(text)

        prev_end = end

    if current and current["texts"]:
        grouped_text = _clean_text(" ".join(current["texts"]))
        if grouped_text:
            groups.append({
                "start": round(float(current["start"]), 3),
                "end": round(float(current["end"]), 3),
                "duration": round(float(current["end"]) - float(current["start"]), 3),
                "text": grouped_text,
            })
    return groups

# ---------------- Whisper transcription ----------------

def transcribe_whisper(
    audio_path: Path,
    model_name: str,
    language: Optional[str],
    device: str,
    compute_type: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=False,                 # VAD OFF as requested
        word_timestamps=False,
    )
    seg_list = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    meta = {
        "detected_language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "model_name": model_name,
        "device": device,
        "compute_type": compute_type,
    }
    return seg_list, meta

# ---------------- Writers ----------------

def write_txt(groups: List[Dict[str, Any]], out_txt: Path) -> None:
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(g["text"] for g in groups), encoding="utf-8")

def write_csv(groups: List[Dict[str, Any]], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start", "end", "duration", "text"])
        for g in groups:
            w.writerow([g["start"], g["end"], g["duration"], g["text"]])

def write_json(
    groups: List[Dict[str, Any]],
    raw_segments: List[Dict[str, Any]],
    meta: Dict[str, Any],
    out_json: Path
) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "groups": groups, "segments": raw_segments}
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="Whisper-only karaoke phrase extractor with leading-silence handling.")
    ap.add_argument("--audio", required=True, help="Path to vocals audio (wav/mp3/m4a).")
    ap.add_argument("--out-dir", default="lyrics", help="Output directory. Default: lyrics")
    ap.add_argument("--prefix", default=None, help="Output filename prefix. Default: audio stem")

    # Whisper settings
    ap.add_argument("--model", default="small", help="Whisper model: tiny|base|small|medium|large-v3")
    ap.add_argument("--language", default=None, help="Force language code like 'en'. If omitted, auto-detect.")
    ap.add_argument("--device", default="auto", help="auto|cpu|cuda|metal")
    ap.add_argument("--compute-type", default="int8_float16", help="int8|int8_float16|int16|float16|float32")

    # Silence detection knobs
    ap.add_argument("--silence-threshold", type=float, default=-38.0, help="dB threshold for silence, e.g., -38.0")
    ap.add_argument("--min-silence", type=float, default=0.30, help="Minimum silence length to detect at start, seconds")

    # Grouping knobs
    ap.add_argument("--gap", type=float, default=0.8, help="Gap threshold seconds between segments")
    ap.add_argument("--max-duration", type=float, default=7.0, help="Max phrase duration seconds")
    ap.add_argument("--min-punct-break", type=float, default=2.0, help="Min seconds before breaking on end punctuation")
    ap.add_argument("--max-chars", type=int, default=120, help="Max characters per grouped phrase")

    args = ap.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio not found: {audio_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    prefix = args.prefix or audio_path.stem
    out_txt = out_dir / f"{prefix}_phrases.txt"
    out_csv = out_dir / f"{prefix}_phrases.csv"
    out_json = out_dir / f"{prefix}_phrases.json"

    # 1) Detect leading silence
    lead_s = detect_leading_silence_seconds(
        audio_path,
        noise_db=float(args.silence_threshold),
        min_silence_sec=float(args.min_silence),
    )

    # 2) Make trimmed mono 16k temp wav (keep internal silences)
    trimmed = make_trimmed_for_whisper(audio_path, lead_s)

    # 3) Transcribe with Whisper (VAD OFF)
    raw_segments, meta = transcribe_whisper(
        audio_path=trimmed,
        model_name=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
    )

    if not raw_segments:
        # Fallback: try forcing English if not set
        if not args.language:
            raw_segments, meta = transcribe_whisper(
                audio_path=trimmed,
                model_name=args.model,
                language="en",
                device=args.device,
                compute_type=args.compute_type,
            )
        if not raw_segments:
            raise SystemExit("No segments returned from Whisper (even after fallback).")

    # 4) Shift timestamps back by leading silence so times are from original file start
    segs_shifted = [
        {"start": round(s["start"] + lead_s, 3), "end": round(s["end"] + lead_s, 3), "text": s["text"]}
        for s in raw_segments
    ]

    # 5) Group into phrases
    groups = group_segments(
        segs_shifted,
        gap_threshold=args.gap,
        max_duration=args.max_duration,
        min_duration_for_punct_break=args.min_punct_break,
        max_chars=args.max_chars,
    )

    # 6) Write outputs
    write_txt(groups, out_txt)
    write_csv(groups, out_csv)
    # Include offset and preprocessing info in meta for reproducibility
    meta_out = dict(meta)
    meta_out.update({
        "leading_silence_seconds": round(lead_s, 3),
        "preprocess": {"seek_start": round(lead_s, 3), "mono": True, "sample_rate": 16000},
    })
    write_json(groups, segs_shifted, meta_out, out_json)

    print("OK")
    print(f"Leading silence: {lead_s:.3f}s")
    print(f"TXT : {out_txt}")
    print(f"CSV : {out_csv}")
    print(f"JSON: {out_json}")

if __name__ == "__main__":
    main()

# end of whisper_only_karaoke.py
