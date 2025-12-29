#!/usr/bin/env python3
"""
Estimate the time (seconds) of the very first *word* in a song with minimal Whisper compute.

Music‑tuned version:
- Energy detector thresholds adjusted for *soft vocal entrances*
- Still minimizes Whisper usage
"""

import argparse
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List

import numpy as np


try:
    from faster_whisper import WhisperModel
except Exception as e:
    raise SystemExit(
        "Missing dependency faster-whisper. Install with:\n"
        "  pip3 install faster-whisper\n"
        f"Original error: {e}"
    )


@dataclass
class FirstWordResult:
    first_word_time_secs: float
    first_word: str
    confidence: Optional[float] = None


def _ffmpeg_decode_s16le_16k_mono(audio_path: str, bandpass: bool = True) -> np.ndarray:
    af = []
    if bandpass:
        af.append("highpass=f=80")
        af.append("lowpass=f=6000")
    af_str = ",".join(af) if af else "anull"

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", audio_path, "-ac", "1", "-ar", "16000",
        "-af", af_str, "-f", "s16le", "pipe:1",
    ]
    p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    pcm16 = np.frombuffer(p.stdout, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    pad = win // 2
    xpad = np.pad(x, (pad, pad), mode="edge")
    kernel = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(xpad, kernel, mode="valid")


def _find_voiced_candidates_energy(
    audio_16k: np.ndarray,
    *,
    sr: int = 16000,
    hop_ms: float = 10.0,
    smooth_ms: float = 60.0,
    max_scan_secs: float = 300.0,
    max_candidates: int = 6,
    thresh_db_above_floor: float = 6.0,   # MUSIC‑TUNED (was 12.0)
    min_sustain_ms: float = 200.0,        # MUSIC‑TUNED (was 350.0)
) -> List[float]:
    max_samples = int(min(len(audio_16k), max_scan_secs * sr))
    if max_samples <= 0:
        return []

    x = audio_16k[:max_samples]
    env = np.abs(x).astype(np.float32)

    hop = max(1, int(sr * hop_ms / 1000.0))
    env_h = env[::hop]
    smooth_win = max(1, int((smooth_ms / hop_ms)))
    env_s = _moving_average(env_h, smooth_win)

    db = 20.0 * np.log10(env_s + 1e-6)

    first_n = int(min(len(db), (60.0 / (hop_ms / 1000.0))))
    floor = float(np.percentile(db[:first_n] if first_n > 10 else db, 20.0))
    thresh = floor + thresh_db_above_floor

    sustain_frames = max(1, int(min_sustain_ms / hop_ms))

    candidates: List[float] = []
    run = 0
    in_region = False
    region_start_i = 0

    for i in range(len(db)):
        above = db[i] >= thresh
        if above:
            if not in_region:
                in_region = True
                region_start_i = i
                run = 1
            else:
                run += 1

            if run == sustain_frames:
                t = region_start_i * (hop_ms / 1000.0)
                candidates.append(float(t))
                if len(candidates) >= max_candidates:
                    break
        else:
            in_region = False
            run = 0

    return candidates


def _ffmpeg_extract_s16le_16k_mono(audio_path: str, start_sec: float, duration_sec: float) -> Tuple[np.ndarray, int]:
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}",
        "-i", audio_path, "-ac", "1", "-ar", "16000",
        "-f", "s16le", "pipe:1",
    ]
    p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    pcm16 = np.frombuffer(p.stdout, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0, 16000


def estimate_first_word_time(
    audio_path: str,
    *,
    model_size: str = "tiny",
    language: Optional[str] = None,
    pre_roll_secs: float = 2.0,
    window_secs: float = 16.0,
    max_whisper_windows: int = 6,
    min_time_secs: Optional[float] = None,
    verbose: bool = False,
) -> Optional[FirstWordResult]:
    audio_16k = _ffmpeg_decode_s16le_16k_mono(audio_path)

    candidates = _find_voiced_candidates_energy(audio_16k)
    if min_time_secs is not None:
        try:
            mt = float(min_time_secs)
            candidates = [t for t in candidates if t >= mt]
        except Exception:
            pass
    if verbose:
        print(f"[DEBUG] energy_candidates={['%.2f' % c for c in candidates]}")

    if not candidates:
        return None

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    for i, t0 in enumerate(candidates[:max_whisper_windows]):
        clip_start = max(0.0, t0 - pre_roll_secs)
        clip, _ = _ffmpeg_extract_s16le_16k_mono(audio_path, clip_start, window_secs)

        segments, _ = model.transcribe(
            clip,
            language=language,
            beam_size=1,
            vad_filter=False,
            word_timestamps=True,
            condition_on_previous_text=False,
            temperature=0.0,
        )

        for seg in segments:
            if not seg.words:
                continue
            w = seg.words[0]
            if w.word and len(w.word.strip()) > 1:
                return FirstWordResult(
                    first_word_time_secs=clip_start + float(w.start or seg.start),
                    first_word=w.word.strip(),
                    confidence=getattr(w, "probability", None),
                )

    return None


def _fmt_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - (m * 60)
    return f"{m:02d}:{s:05.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Find rough first-word time (music tuned)")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--language", default=None)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    res = estimate_first_word_time(
        args.audio,
        language=args.language,
        verbose=args.verbose,
    )

    if res is None:
        print("No first-word time detected.")
        return 2

    print(f"first_word_time_secs={res.first_word_time_secs:.3f}")
    print(f"first_word_time_mmss={_fmt_mmss(res.first_word_time_secs)}")
    print(f"first_word={res.first_word}")
    if res.confidence is not None:
        print(f"confidence={res.confidence:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# end of first_word_time.py
