#!/usr/bin/env python3
"""
Estimate the time (seconds) of the very first *word* in a song with minimal Whisper compute.

Goal: minimize compute time (Whisper used as little as possible).

Fast strategy:
1) Decode to 16 kHz mono PCM and run aggressive WebRTC VAD + RMS gating to find a short list
   of candidate "first speech-ish" times.
2) Run faster-whisper on ONLY a few tiny windows (default: 3 windows x 8s) around those candidates.
3) Reject likely hallucinations using no_speech_prob / avg_logprob / word probability heuristics.
4) Return the timestamp of the earliest accepted first word (roughly).

Requirements:
  - ffmpeg installed and on PATH
  - pip3 install faster-whisper webrtcvad numpy

Notes:
  - Best results if you run this on a vocals stem (e.g., Demucs vocals), but it works on mixes too.
  - This returns a *rough* first-word time, not sample-accurate alignment.
"""

import argparse
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List

import numpy as np

try:
    import webrtcvad
except Exception as e:
    raise SystemExit(
        "Missing dependency webrtcvad. Install with:\n"
        "  pip3 install webrtcvad\n"
        f"Original error: {e}"
    )

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
    """
    Decode audio to mono 16kHz int16 PCM using ffmpeg.
    Optionally band-pass filter to help VAD focus on voice-ish frequencies.
    Returns float32 samples in [-1, 1].
    """
    af = []
    if bandpass:
        # Speech-ish band (helps VAD ignore sub-bass + very high cymbals)
        af.append("highpass=f=120")
        af.append("lowpass=f=4000")
    af_str = ",".join(af) if af else "anull"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-i", audio_path,
        "-ac", "1",
        "-ar", "16000",
        "-af", af_str,
        "-f", "s16le",
        "pipe:1",
    ]
    p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    pcm16 = np.frombuffer(p.stdout, dtype=np.int16)
    if pcm16.size == 0:
        raise RuntimeError("ffmpeg produced no audio samples")
    return pcm16.astype(np.float32) / 32768.0


def _find_voiced_candidates_webrtcvad(
    audio_16k: np.ndarray,
    *,
    frame_ms: int = 30,
    vad_mode: int = 3,
    min_consecutive_voiced_frames: int = 14,
    max_scan_secs: float = 180.0,
    max_candidates: int = 8,
    rms_gate_db_above_median: float = 4.0,
) -> List[float]:
    """
    Return a small list of candidate "first voice-ish" times.

    Uses:
    - Aggressive WebRTC VAD (mode 3)
    - RMS gating relative to the median RMS within the scan region

    Output times are in seconds since start of the track.
    """
    if frame_ms not in (10, 20, 30):
        raise ValueError("frame_ms must be 10, 20, or 30")

    vad = webrtcvad.Vad(vad_mode)
    sr = 16000
    frame_len = int(sr * frame_ms / 1000)
    max_samples = int(min(len(audio_16k), max_scan_secs * sr))

    pcm16 = (np.clip(audio_16k[:max_samples], -1.0, 1.0) * 32767.0).astype(np.int16)

    n_frames = (len(pcm16) - frame_len) // frame_len + 1
    if n_frames <= 0:
        return []

    # Per-frame RMS
    rms = np.empty(n_frames, dtype=np.float32)
    for fi in range(n_frames):
        start = fi * frame_len
        frame = pcm16[start : start + frame_len].astype(np.float32) / 32768.0
        rms[fi] = float(np.sqrt(np.mean(frame * frame) + 1e-12))

    med = float(np.median(rms))
    gate = med * (10 ** (rms_gate_db_above_median / 20.0))

    raw = pcm16.tobytes()
    candidates: List[float] = []

    voiced_run = 0
    in_region = False
    region_start_fi = 0

    for fi in range(n_frames):
        start = fi * frame_len
        frame_bytes = raw[start * 2 : (start + frame_len) * 2]  # int16 -> 2 bytes each

        is_voiced = vad.is_speech(frame_bytes, sr) and (rms[fi] >= gate)

        if is_voiced:
            if not in_region:
                in_region = True
                region_start_fi = fi
                voiced_run = 1
            else:
                voiced_run += 1

            if voiced_run == min_consecutive_voiced_frames:
                # Candidate at region start (earliest)
                t = region_start_fi * (frame_ms / 1000.0)
                candidates.append(t)
                if len(candidates) >= max_candidates:
                    break
        else:
            in_region = False
            voiced_run = 0

    return candidates


def _ffmpeg_extract_s16le_16k_mono(audio_path: str, start_sec: float, duration_sec: float) -> Tuple[np.ndarray, int]:
    """
    Extract a small segment and return float32 samples at 16kHz mono.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration_sec:.3f}",
        "-i", audio_path,
        "-ac", "1",
        "-ar", "16000",
        "-f", "s16le",
        "pipe:1",
    ]
    p = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    pcm16 = np.frombuffer(p.stdout, dtype=np.int16)
    return pcm16.astype(np.float32) / 32768.0, 16000


def _first_word_from_segments(segments) -> Tuple[Optional[object], Optional[object]]:
    """
    segments is an iterable of faster-whisper segment objects.
    Returns (segment, word) for the earliest non-empty word.
    """
    for seg in segments:
        words = getattr(seg, "words", None)
        if not words:
            continue
        for w in words:
            word = (getattr(w, "word", "") or "").strip()
            if word:
                return seg, w
    return None, None


def _looks_like_real_speech(seg, w) -> bool:
    """
    Reject likely hallucinations/music-as-speech quickly.
    Heuristics are intentionally strict to avoid early false positives.
    """
    avg_logprob = getattr(seg, "avg_logprob", None)
    no_speech_prob = getattr(seg, "no_speech_prob", None)
    wprob = getattr(w, "probability", None)

    if no_speech_prob is not None and no_speech_prob > 0.60:
        return False

    if avg_logprob is not None and avg_logprob < -1.20:
        return False

    if wprob is not None and wprob < 0.25:
        return False

    word = (getattr(w, "word", "") or "").strip()
    if len(word) <= 1:
        return False

    return True


def estimate_first_word_time(
    audio_path: str,
    *,
    model_size: str = "tiny",
    device: str = "cpu",
    compute_type: str = "int8",
    language: Optional[str] = None,
    max_scan_secs: float = 180.0,
    pre_roll_secs: float = 1.0,
    window_secs: float = 8.0,
    vad_bandpass: bool = True,
    max_whisper_windows: int = 3,
    verbose: bool = False,
) -> Optional[FirstWordResult]:
    """
    Return FirstWordResult or None.

    Whisper work is minimized by limiting inference to max_whisper_windows short clips.
    """
    audio_16k = _ffmpeg_decode_s16le_16k_mono(audio_path, bandpass=vad_bandpass)

    candidates = _find_voiced_candidates_webrtcvad(
        audio_16k,
        frame_ms=30,
        vad_mode=3,
        min_consecutive_voiced_frames=14,
        max_scan_secs=max_scan_secs,
        max_candidates=8,
        rms_gate_db_above_median=4.0,
    )
    if verbose:
        print(f"[DEBUG] candidates={['%.2f' % c for c in candidates]}")

    if not candidates:
        return None

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    tried = 0
    for t_voiced in candidates:
        clip_start = max(0.0, t_voiced - pre_roll_secs)
        clip, _sr = _ffmpeg_extract_s16le_16k_mono(audio_path, clip_start, window_secs)

        segments, _info = model.transcribe(
            clip,
            language=language,
            beam_size=1,
            vad_filter=False,
            word_timestamps=True,
            condition_on_previous_text=False,
            temperature=0.0,
        )

        seg, w = _first_word_from_segments(segments)
        if seg is not None and w is not None:
            if verbose:
                print(
                    f"[DEBUG] clip_start={clip_start:.2f} seg.start={getattr(seg,'start',None)} "
                    f"no_speech_prob={getattr(seg,'no_speech_prob',None)} avg_logprob={getattr(seg,'avg_logprob',None)} "
                    f"word='{getattr(w,'word',None)}' wprob={getattr(w,'probability',None)}"
                )

            if _looks_like_real_speech(seg, w):
                w_start = getattr(w, "start", None)
                seg_start = getattr(seg, "start", 0.0)
                t_word = clip_start + float(w_start if w_start is not None else seg_start)
                return FirstWordResult(
                    first_word_time_secs=t_word,
                    first_word=(getattr(w, "word", "") or "").strip(),
                    confidence=getattr(w, "probability", None),
                )

        tried += 1
        if tried >= max_whisper_windows:
            break

    return None


def _fmt_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - (m * 60)
    return f"{m:02d}:{s:05.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Find rough first-word time with minimal Whisper compute")
    ap.add_argument("--audio", required=True, help="Path to audio file (mp3/wav/etc). Prefer vocals stem if available.")
    ap.add_argument("--model", default="tiny", help="faster-whisper model size (tiny/base/small/...). Default tiny for speed.")
    ap.add_argument("--language", default=None, help='Optional fixed language code, e.g. "en" or "es" (faster than autodetect).')
    ap.add_argument("--max-scan-secs", type=float, default=180.0, help="How far into the track to scan with VAD.")
    ap.add_argument("--pre-roll", type=float, default=1.0, help="Seconds before candidate voice to include in the Whisper window.")
    ap.add_argument("--window", type=float, default=8.0, help="Whisper window length in seconds.")
    ap.add_argument("--max-windows", type=int, default=3, help="Maximum Whisper windows to try (keeps compute low).")
    ap.add_argument("--no-bandpass", action="store_true", help="Disable band-pass filtering for VAD.")
    ap.add_argument("--verbose", action="store_true", help="Print debug info.")
    args = ap.parse_args()

    res = estimate_first_word_time(
        args.audio,
        model_size=args.model,
        language=args.language,
        max_scan_secs=args.max_scan_secs,
        pre_roll_secs=args.pre_roll,
        window_secs=args.window,
        vad_bandpass=not args.no_bandpass,
        max_whisper_windows=args.max_windows,
        verbose=args.verbose,
    )

    if res is None:
        print("No first-word time detected (VAD/Whisper returned nothing reliable).")
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
