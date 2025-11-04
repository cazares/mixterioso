#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Evaluate timestamp stability on audio with gaps using two pipelines:
1) Faster-Whisper (VAD only)
2) WhisperX (VAD + forced alignment), if installed

Outputs:
- poc_out/faster.srt (if run)
- poc_out/whisperx.srt (if run)
- poc_out/report.json with WER and boundary timing errors

Run:
  python3 eval_poc.py path/to/audio.mp3 --ref-lyrics lyrics/song.txt
"""

import argparse, json, math, os, sys
from dataclasses import dataclass
from typing import List, Tuple, Optional

# --- optional deps guarded at call time ---
def _have(pkg: str) -> bool:
    try:
        __import__(pkg)
        return True
    except Exception:
        return False

# jiwer is optional for WER scoring
_HAVE_JIWER = _have("jiwer")

import numpy as np
import librosa
import soundfile as sf  # noqa: F401  # ensures libsndfile present

# ---------- utils ----------
def ts_str(t: Optional[float]) -> str:
    if t is None:
        return "00:00:00,000"
    ms = int(round(float(t) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def write_srt(segs: List[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{ts_str(s.get('start'))} --> {ts_str(s.get('end'))}\n{s.get('text','').strip()}\n\n")

def normalize_text(t: str) -> str:
    return " ".join("".join(ch.lower() for ch in t if ch.isalnum() or ch.isspace()).split())

def segs_to_text(segs: List[dict]) -> str:
    return normalize_text(" ".join(s.get("text","") for s in segs))

@dataclass
class Scores:
    wer: float
    mean_boundary_abs_err_ms: float
    median_boundary_abs_err_ms: float
    ran: bool

def energy_boundaries(
    audio_path: str,
    sr_target: int = 16000,
    hop_ms: int = 20,
    silence_db: float = -40.0,
    min_gap_ms: int = 600,
) -> List[float]:
    y, sr = librosa.load(audio_path, sr=sr_target, mono=True)
    hop = int(sr * hop_ms / 1000.0)
    frame_len = max(2 * hop, 2048)
    rms = librosa.feature.rms(y=y, frame_length=frame_len, hop_length=hop, center=True)[0]
    db = librosa.amplitude_to_db(rms + 1e-9, ref=np.max)
    is_sil = db < silence_db

    boundaries = []
    run = 0
    for i, s in enumerate(is_sil):
        if s:
            run += 1
        else:
            if run * hop_ms >= min_gap_ms:
                boundaries.append(i * hop_ms / 1000.0)  # seconds
            run = 0
    return boundaries

def boundary_error(seg_boundaries: List[float], energy_bounds: List[float]) -> Tuple[float, float]:
    if not seg_boundaries or not energy_bounds:
        return math.nan, math.nan
    diffs = []
    j = 0
    for b in seg_boundaries:
        while j + 1 < len(energy_bounds) and abs(energy_bounds[j + 1] - b) <= abs(energy_bounds[j] - b):
            j += 1
        diffs.append(abs(energy_bounds[j] - b) * 1000.0)
    arr = np.array(diffs, dtype=float)
    return float(np.nanmean(arr)), float(np.nanmedian(arr))

def score(audio_path: str, segs: List[dict], ref_txt: Optional[str]) -> Scores:
    # WER vs optional reference
    w = float("nan")
    if ref_txt and _have("jiwer"):
        from jiwer import wer as _wer
        with open(ref_txt, "r", encoding="utf-8") as f:
            ref = normalize_text(f.read())
        hyp = segs_to_text(segs)
        w = _wer(ref, hyp)

    # timing score: compare segment starts to energy-based boundaries
    seg_starts = [s.get("start") for s in segs if s.get("start") is not None]
    eb = energy_boundaries(audio_path)
    mean_ms, med_ms = boundary_error(seg_starts, eb)
    return Scores(wer=w, mean_boundary_abs_err_ms=mean_ms, median_boundary_abs_err_ms=med_ms, ran=True)

# ---------- pipelines ----------
def run_faster_whisper(audio_path: str) -> List[dict]:
    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    seg_iter, _info = model.transcribe(
        audio_path,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=1000),
        temperature=0.0,
        condition_on_previous_text=False,
        task="transcribe",
    )
    segs = []
    for s in seg_iter:
        segs.append({"start": float(s.start), "end": float(s.end), "text": s.text})
    return segs

def run_whisperx(audio_path: str) -> List[dict]:
    import whisperx
    device = "cpu"
    asr = whisperx.load_model("large-v3", device)
    res = asr.transcribe(audio_path, vad=True)
    align_model, meta = whisperx.load_align_model(language_code=res["language"], device=device)
    aligned = whisperx.align(res["segments"], align_model, meta, audio_path, device)
    segs = []
    for s in aligned["segments"]:
        segs.append({"start": float(s.get("start")), "end": float(s.get("end")), "text": s.get("text","")})
    return segs

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="Path to .mp3 or .wav")
    ap.add_argument("--ref-lyrics", help="Optional TXT with ground-truth lyrics for WER", default=None)
    ap.add_argument("--outdir", default="poc_out")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    report = {
        "audio": args.audio,
        "ref_lyrics": args.ref_lyrics,
        "notes": "Lower WER and lower median boundary error (ms) are better. Boundary error compares segment starts to energy onsets after >=600 ms silence.",
        "faster_whisper": None,
        "whisperx": None,
    }

    # Faster-Whisper
    try:
        fw_segs = run_faster_whisper(args.audio)
        write_srt(fw_segs, os.path.join(args.outdir, "faster.srt"))
        fw_scores = score(args.audio, fw_segs, args.ref_lyrics)
        report["faster_whisper"] = dict(
            ran=fw_scores.ran,
            wer=fw_scores.wer,
            mean_ms=fw_scores.mean_boundary_abs_err_ms,
            median_ms=fw_scores.median_boundary_abs_err_ms,
        )
    except Exception as e:
        report["faster_whisper"] = {"ran": False, "error": repr(e)}

    # WhisperX (optional)
    if _have("whisperx"):
        try:
            wx_segs = run_whisperx(args.audio)
            write_srt(wx_segs, os.path.join(args.outdir, "whisperx.srt"))
            wx_scores = score(args.audio, wx_segs, args.ref_lyrics)
            report["whisperx"] = dict(
                ran=wx_scores.ran,
                wer=wx_scores.wer,
                mean_ms=wx_scores.mean_boundary_abs_err_ms,
                median_ms=wx_scores.median_boundary_abs_err_ms,
            )
        except Exception as e:
            report["whisperx"] = {"ran": False, "error": repr(e)}
    else:
        report["whisperx"] = {"ran": False, "error": "whisperx not installed in this env"}

    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
# end of eval_poc.py
