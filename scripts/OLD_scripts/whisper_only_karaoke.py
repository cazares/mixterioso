#!/usr/bin/env python3
# whisper_only_karaoke.py
# Full-mix or vocals-only → trim leading silence (start only) → mono16k temp
# → Faster-Whisper (VAD OFF, word timestamps ON) → group phrases → shift times
# back by leading-silence offset → write TXT/CSV/JSON.
#
# CSV schema: start,end,duration,text  (times from ORIGINAL file start)
# TXT: grouped phrases, one per line
# JSON: {meta, groups, segments, words}
#
# Accuracy tips:
#   --model large-v3  --device cpu  --compute-type float32  --beam-size 5 --best-of 5 --temperature 0.0
#   Keep VAD off. Run on FULL MIX for ASR; use Demucs later only for audio.

import argparse
import csv
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------- Silence detection (leading only) ----------------

def detect_leading_silence_seconds(
    audio_path: Path, noise_db: float = -38.0, min_silence_sec: float = 0.30
) -> float:
    """
    Returns leading silence length in seconds via ffmpeg silencedetect.
    Only considers an initial silence that begins at t≈0.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(audio_path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log = proc.stderr  # silencedetect logs to stderr

    saw_start_zero = False
    for line in log.splitlines():
        line = line.strip()
        if line.startswith("silence_start:"):
            try:
                val = float(line.split("silence_start:")[1].strip())
                if abs(val) < 1e-3:
                    saw_start_zero = True
            except Exception:
                pass
        if saw_start_zero and line.startswith("silence_end:"):
            try:
                parts = line.split("silence_end:")[1].strip().split("|")[0].strip()
                return float(parts)
            except Exception:
                break
    return 0.0

def make_trimmed_mono16k(src: Path, lead_s: float) -> Path:
    """
    Create a temp WAV that starts after leading silence (internal silences untouched).
    Output is mono @ 16 kHz for fastest inference.
    """
    tmp = Path(tempfile.gettempdir()) / f"{src.stem}_mono16k_trim.wav"
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{lead_s:.3f}",     # seek past leading silence
        "-i", str(src),
        "-ac", "1", "-ar", "16000",
        str(tmp)
    ]
    subprocess.run(cmd, check=True)
    return tmp

# ---------------- Text utils + grouping ----------------

_MUSIC_ALIASES = {"music", "[music]", "(music)", "instrumental", "[instrumental]"}

def _clean_text(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\s*\[.*?\]\s*", " ", t)   # drop bracketed tags
    t = re.sub(r"\s*<.*?>\s*", " ", t)     # drop angle-tagged tokens
    t = t.replace("♪", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def _is_music_token(t: str) -> bool:
    n = t.lower().strip()
    return n in _MUSIC_ALIASES

def group_segments(
    segs: List[Dict[str, Any]],
    gap_threshold: float = 0.8,
    max_duration: float = 7.0,
    min_duration_for_punct_break: float = 2.0,
    max_chars: int = 120,
    replace_music_emoji: bool = True,
) -> List[Dict[str, Any]]:
    """
    Group Whisper segments into karaoke-friendly phrases.
    Break on: large gap, max duration, max chars, or end punctuation after a few seconds.
    """
    groups: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    prev_end: Optional[float] = None
    end_punct_re = re.compile(r"[.!?…]\s*$")

    def _emit(cur):
        grouped_text = _clean_text(" ".join(cur["texts"]))
        if not grouped_text:
            return
        if replace_music_emoji and _is_music_token(grouped_text):
            grouped_text = "🎶"
        groups.append({
            "start": round(float(cur["start"]), 3),
            "end": round(float(cur["end"]), 3),
            "duration": round(float(cur["end"]) - float(cur["start"]), 3),
            "text": grouped_text,
        })

    for s in segs:
        start = float(s["start"])
        end = float(s["end"])
        text = _clean_text(str(s.get("text", "")))
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
            _emit(current)
            current = {"start": start, "end": end, "texts": [text]}
        else:
            current["end"] = end
            current["texts"].append(text)

        prev_end = end

    if current and current["texts"]:
        _emit(current)

    return groups

# ---------------- Whisper transcription ----------------

def transcribe_whisper(
    audio_path: Path,
    model_name: str,
    language: Optional[str],
    device: str,
    compute_type: str,
    beam_size: int,
    best_of: int,
    temperature: float,
    initial_prompt: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    """
    Returns: (segments_list, meta, words_list)
      segments_list: [{start,end,text}]
      words_list:    [{start,end,word}]  (flattened across segments)
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=False,                # VAD OFF
        word_timestamps=True,            # <-- word timings ON
        beam_size=beam_size,
        best_of=best_of,
        temperature=temperature,
        initial_prompt=initial_prompt if initial_prompt else None,
    )

    seg_list: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    for s in segments:
        seg_list.append({"start": s.start, "end": s.end, "text": s.text})
        if s.words:
            for w in s.words:
                # Some models can emit None for start/end on short tokens; guard it.
                if w.start is None or w.end is None:
                    continue
                words.append({"start": w.start, "end": w.end, "word": w.word})

    meta = {
        "detected_language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "model_name": model_name,
        "device": device,
        "compute_type": compute_type,
        "beam_size": beam_size,
        "best_of": best_of,
        "temperature": temperature,
    }
    return seg_list, meta, words

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
    words: List[Dict[str, Any]],
    meta: Dict[str, Any],
    out_json: Path
) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "groups": groups, "segments": raw_segments, "words": words}
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------------- CLI ----------------

def main():
    ap = argparse.ArgumentParser(description="Whisper-only phrase grouper with leading-silence handling and word timestamps.")
    ap.add_argument("--audio", required=True, help="Path to audio (full mix preferred).")
    ap.add_argument("--out-dir", default="whisper_timings", help="Output directory. Default: whisper_timings")
    ap.add_argument("--prefix", default=None, help="Output filename prefix. Default: audio stem")

    # Whisper
    ap.add_argument("--model", default="small", help="Whisper model: tiny|base|small|medium|large-v3")
    ap.add_argument("--language", default=None, help="Force language code like 'en'. If omitted, auto-detect.")
    ap.add_argument("--device", default="cpu", help="cpu|cuda  (ctranslate2/faster-whisper has no 'metal')")
    ap.add_argument("--compute-type", default="int8", help="int8|int8_float16|int16|float16|float32")

    # Decode knobs
    ap.add_argument("--beam-size", type=int, default=5, help="Beam size for beam search.")
    ap.add_argument("--best-of", type=int, default=5, help="Number of candidates when sampling (used if beam_size=1).")
    ap.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature (0.0 for deterministic).")
    ap.add_argument("--initial-prompt", default=None, help="Optional priming text to bias decoding.")

    # Silence detection
    ap.add_argument("--silence-threshold", type=float, default=-38.0, help="dB threshold for leading silence, e.g., -38.0")
    ap.add_argument("--min-silence", type=float, default=0.30, help="Minimum silence length to detect at start, seconds")

    # Grouping
    ap.add_argument("--gap", type=float, default=0.8, help="Gap threshold seconds between segments")
    ap.add_argument("--max-duration", type=float, default=7.0, help="Max phrase duration seconds")
    ap.add_argument("--min-punct-break", type=float, default=2.0, help="Min seconds before breaking on end punctuation")
    ap.add_argument("--max-chars", type=int, default=120, help="Max characters per grouped phrase")
    ap.add_argument("--music-emoji", action="store_true", help="Replace 'Music' tokens with 🎶 in outputs")

    args = ap.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    if not audio_path.exists():
        raise SystemExit(f"Audio not found: {audio_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    prefix = args.prefix or audio_path.stem
    out_txt  = out_dir / f"{prefix}_phrases.txt"
    out_csv  = out_dir / f"{prefix}_phrases.csv"
    out_json = out_dir / f"{prefix}_phrases.json"

    # 1) Detect leading silence on the SAME file we transcribe
    lead_s = detect_leading_silence_seconds(
        audio_path,
        noise_db=float(args.silence_threshold),
        min_silence_sec=float(args.min_silence),
    )

    # 2) Make trimmed mono16k temp (internal silences preserved)
    trimmed = make_trimmed_mono16k(audio_path, lead_s)

    # 3) Transcribe with Faster-Whisper (VAD OFF, word timestamps ON)
    raw_segments, meta, words = transcribe_whisper(
        audio_path=trimmed,
        model_name=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        best_of=args.best_of,
        temperature=args.temperature,
        initial_prompt=args.initial_prompt,
    )

    # Fallback: force English once if empty
    if not raw_segments and not words and not args.language:
        raw_segments, meta, words = transcribe_whisper(
            audio_path=trimmed,
            model_name=args.model,
            language="en",
            device=args.device,
            compute_type=args.compute_type,
            beam_size=args.beam_size,
            best_of=args.best_of,
            temperature=args.temperature,
            initial_prompt=args.initial_prompt,
        )
    if not raw_segments and not words:
        raise SystemExit("No segments/words returned from Whisper.")

    # 4) Shift timestamps back by leading silence to original timeline
    segs_shifted = [
        {"start": round(s["start"] + lead_s, 3), "end": round(s["end"] + lead_s, 3), "text": s["text"]}
        for s in raw_segments
    ]
    words_shifted = [
        {"start": round(w["start"] + lead_s, 3), "end": round(w["end"] + lead_s, 3), "word": w["word"]}
        for w in words
    ]

    # 5) Group into phrases (with optional 🎶)
    groups = group_segments(
        segs_shifted,
        gap_threshold=args.gap,
        max_duration=args.max_duration,
        min_duration_for_punct_break=args.min_punct_break,
        max_chars=args.max_chars,
        replace_music_emoji=bool(args.music_emoji),
    )

    # 6) Write outputs
    meta_out = dict(meta)
    meta_out.update({
        "leading_silence_seconds": round(lead_s, 3),
        "preprocess": {"seek_start": round(lead_s, 3), "mono": True, "sample_rate": 16000},
        "notes": "VAD off; word_timestamps on; grouped on segment timings.",
    })
    write_txt(groups, out_txt)
    write_csv(groups, out_csv)
    write_json(groups, segs_shifted, words_shifted, meta_out, out_json)

    print("OK")
    print(f"Leading silence: {lead_s:.3f}s")
    print(f"TXT : {out_txt}")
    print(f"CSV : {out_csv}")
    print(f"JSON: {out_json}")

if __name__ == "__main__":
    main()

# end of whisper_only_karaoke.py
