#!/usr/bin/env python3
# vocal_first_whisper.py
# 1) Isolate vocals with Demucs. 2) Transcribe vocals with Faster-Whisper.
# Outputs: <prefix>_phrases.txt / .csv / .json in --out-dir.

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------- text utils ----------
def _clean_text(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\s*\[.*?\]\s*", " ", t)   # [Music], [Applause]
    t = re.sub(r"\s*<.*?>\s*", " ", t)     # <unk>
    t = t.replace("♪", " ")
    t = re.sub(r"\s+", " ", t)
    return t.strip()

def _group_segments(
    segs: List[Dict[str, Any]],
    gap_threshold: float = 0.8,
    max_duration: float = 7.0,
    min_duration_for_punct_break: float = 2.0,
    max_chars: int = 120,
) -> List[Dict[str, Any]]:
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
        prospective_end = end
        duration_if_added = prospective_end - float(current["start"])
        chars_if_added = sum(len(t) for t in current["texts"]) + len(text)
        end_punct = bool(end_punct_re.search(text))

        should_break = False
        if gap >= gap_threshold:
            should_break = True
        elif duration_if_added > max_duration:
            should_break = True
        elif chars_if_added > max_chars:
            should_break = True
        elif end_punct and duration_if_added >= min_duration_for_punct_break:
            should_break = True

        if should_break:
            grouped_text = _clean_text(" ".join(current["texts"]))
            if grouped_text:
                groups.append(
                    {
                        "start": round(float(current["start"]), 3),
                        "end": round(float(current["end"]), 3),
                        "duration": round(float(current["end"]) - float(current["start"]), 3),
                        "text": grouped_text,
                    }
                )
            current = {"start": start, "end": end, "texts": [text]}
        else:
            current["end"] = end
            current["texts"].append(text)

        prev_end = end

    if current and current["texts"]:
        grouped_text = _clean_text(" ".join(current["texts"]))
        if grouped_text:
            groups.append(
                {
                    "start": round(float(current["start"]), 3),
                    "end": round(float(current["end"]), 3),
                    "duration": round(float(current["end"]) - float(current["start"]), 3),
                    "text": grouped_text,
                }
            )
    return groups

# ---------- demucs ----------
def _which(cmd: str) -> Optional[str]:
    for p in os.environ.get("PATH", "").split(os.pathsep):
        exe = Path(p) / cmd
        if exe.exists() and os.access(exe, os.X_OK):
            return str(exe)
    return None

def run_demucs(
    audio_path: Path,
    model: str = "htdemucs",
    stems: int = 4,
    out_root: Optional[Path] = None,
    overwrite: bool = False,
) -> Path:
    """
    Calls demucs CLI to separate stems. Returns path to vocals.wav.
    Output convention: separated/<model>/<track-stem>/vocals.wav
    """
    if _which("demucs") is None:
        raise SystemExit("demucs not found. Install with: pip3 install demucs")

    track_stem = audio_path.stem
    out_root = out_root or Path.cwd() / "separated"
    model_arg = model
    stem_flag = ["--two-stems", "vocals"] if stems == 2 else []
    # demucs supports 4-stem and 6-stem via model choice; for 6 stems use htdemucs_6s model.
    cmd = ["demucs", "-n", model_arg, "-o", str(out_root)]
    if overwrite:
        cmd += ["--overwrite"]
    cmd += stem_flag + [str(audio_path)]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Demucs failed with exit code {e.returncode}")

    # Search likely locations for vocals.wav
    candidates = [
        out_root / model_arg / track_stem / "vocals.wav",
    ]
    # Fallback: any model dir for this track
    candidates += [Path(p) for p in glob(str(out_root / "*" / track_stem / "vocals.wav"))]

    for c in candidates:
        if c.exists():
            return c

    raise SystemExit("Could not find vocals.wav after Demucs. Check model name and stems.")

# ---------- whisper ----------
def transcribe_whisper(
    audio_path: Path,
    model_name: str,
    language: Optional[str],
    device: str,
    compute_type: str,
    vad_filter: bool,
    vad_min_silence_ms: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=vad_filter,
        vad_parameters=dict(min_silence_duration_ms=vad_min_silence_ms),
        word_timestamps=False,
    )
    seg_list = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    meta = {
        "detected_language": getattr(info, "language", None),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "model_name": model_name,
        "source": "vocals_only",
    }
    return seg_list, meta

# ---------- writers ----------
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

def write_json(groups: List[Dict[str, Any]], raw_segments: List[Dict[str, Any]], meta: Dict[str, Any], out_json: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "groups": groups, "segments": raw_segments}
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- cli ----------
def main():
    ap = argparse.ArgumentParser(description="Isolate vocals with Demucs, then transcribe with Faster-Whisper.")
    ap.add_argument("--audio", required=True, help="Path to full mix audio (mp3/wav/m4a/…)")

    # Demucs
    ap.add_argument("--demucs-model", default="htdemucs", help="Demucs model: htdemucs | htdemucs_6s | mdx_extra_q etc.")
    ap.add_argument("--stems", type=int, default=4, help="2 or 4 or 6. For 6, use --demucs-model htdemucs_6s.")
    ap.add_argument("--demucs-out", default=None, help="Optional Demucs output root directory")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite Demucs outputs if present")
    ap.add_argument("--skip-separate", action="store_true", help="Skip separation and use --vocals directly")
    ap.add_argument("--vocals", default=None, help="Path to vocals.wav if already separated")

    # Whisper
    ap.add_argument("--model", default="small", help="Whisper model: tiny|base|small|medium|large-v3")
    ap.add_argument("--language", default=None, help="ISO code like 'en', or omit for auto-detect")
    ap.add_argument("--device", default="auto", help="auto|cpu|cuda|metal")
    ap.add_argument("--compute-type", default="int8_float16", help="int8_float16|int8|int16|float16|float32")
    ap.add_argument("--vad", action="store_true", help="Enable VAD")
    ap.add_argument("--vad-min-silence-ms", type=int, default=300)

    # Grouping
    ap.add_argument("--gap", type=float, default=0.8)
    ap.add_argument("--max-duration", type=float, default=7.0)
    ap.add_argument("--min-punct-break", type=float, default=2.0)
    ap.add_argument("--max-chars", type=int, default=120)

    # Outputs
    ap.add_argument("--out-dir", default="lyrics")
    ap.add_argument("--prefix", default=None)

    args = ap.parse_args()
    audio = Path(args.audio).expanduser().resolve()
    if not audio.exists():
        raise SystemExit(f"Audio not found: {audio}")

    prefix = args.prefix or audio.stem
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_txt = out_dir / f"{prefix}_phrases.txt"
    out_csv = out_dir / f"{prefix}_phrases.csv"
    out_json = out_dir / f"{prefix}_phrases.json"

    # 1) Vocals path
    if args.skip_separate:
        if not args.vocals:
            raise SystemExit("--skip-separate requires --vocals pointing to vocals-only file")
        vocals_path = Path(args.vocals).expanduser().resolve()
        if not vocals_path.exists():
            raise SystemExit(f"Vocals file not found: {vocals_path}")
    else:
        demucs_out = Path(args.demucs_out).expanduser().resolve() if args.demucs_out else None
        vocals_path = run_demucs(
            audio_path=audio,
            model=args.demucs_model,
            stems=args.stems,
            out_root=demucs_out,
            overwrite=args.overwrite,
        )

    # 2) Transcribe vocals
    raw_segments, meta = transcribe_whisper(
        audio_path=vocals_path,
        model_name=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        vad_filter=bool(args.vad),
        vad_min_silence_ms=args.vad_min_silence_ms,
    )
    if not raw_segments:
        raise SystemExit("No segments returned from Whisper.")

    groups = _group_segments(
        raw_segments,
        gap_threshold=args.gap,
        max_duration=args.max_duration,
        min_duration_for_punct_break=args.min_punct_break,
        max_chars=args.max_chars,
    )

    # 3) Write files
    write_txt(groups, out_txt)
    write_csv(groups, out_csv)
    write_json(groups, raw_segments, meta, out_json)

    print("OK")
    print(f"Vocals : {vocals_path}")
    print(f"TXT    : {out_txt}")
    print(f"CSV    : {out_csv}")
    print(f"JSON   : {out_json}")

if __name__ == "__main__":
    main()

# end of vocal_first_whisper.py
