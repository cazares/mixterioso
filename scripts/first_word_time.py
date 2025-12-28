#!/usr/bin/env python3
"""
Estimate the time (seconds) of the very first *word* in a song with minimal Whisper compute.

Key point: WebRTC VAD is trained for *speech*, and can completely miss *singing*.
So this script uses an "auto" detector:
  1) Try WebRTC VAD (fast, good when vocals are speech-like)
  2) If VAD yields no candidates, fall back to a singing-friendly ENERGY detector
     (band-limited amplitude envelope + threshold + sustained-run)

Then run faster-whisper on ONLY a few tiny windows (default: 3 windows x 8s) around candidates
and reject hallucinations using no_speech_prob / avg_logprob / word probability heuristics.

Requirements:
  - ffmpeg installed and on PATH
  - pip3 install faster-whisper numpy webrtcvad

Usage example:
  python3 scripts/first_word_time.py --audio mp3s/the_zephyr_song.mp3 --language en --max-scan-secs 300 --verbose

Notes:
  - Best results if you run this on a vocals stem, but it works on mixes too.
  - This returns a *rough* first-word time, not sample-accurate alignment.
"""

import argparse
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List

import numpy as np

try:
    import webrtcvad
except Exception:
    webrtcvad = None

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
    Optionally band-pass filter to help detectors focus on voice-ish frequencies.
    Returns float32 samples in [-1, 1].
    """
    af = []
    if bandpass:
        # For singing/voice detection, keep a slightly wider band than strict speech.
        af.append("highpass=f=80")
        af.append("lowpass=f=6000")
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
    min_consecutive_voiced_frames: int = 12,
    max_scan_secs: float = 300.0,
    max_candidates: int = 6,
) -> List[float]:
    """
    Candidate detector using WebRTC VAD (speech-trained). May miss singing.
    """
    if webrtcvad is None:
        return []

    if frame_ms not in (10, 20, 30):
        raise ValueError("frame_ms must be 10, 20, or 30")

    vad = webrtcvad.Vad(vad_mode)
    sr = 16000
    frame_len = int(sr * frame_ms / 1000)
    max_samples = int(min(len(audio_16k), max_scan_secs * sr))

    pcm16 = (np.clip(audio_16k[:max_samples], -1.0, 1.0) * 32767.0).astype(np.int16)
    raw = pcm16.tobytes()

    n_frames = (len(pcm16) - frame_len) // frame_len + 1
    if n_frames <= 0:
        return []

    candidates: List[float] = []
    voiced_run = 0
    in_region = False
    region_start_fi = 0

    for fi in range(n_frames):
        start = fi * frame_len
        frame_bytes = raw[start * 2 : (start + frame_len) * 2]

        is_voiced = vad.is_speech(frame_bytes, sr)

        if is_voiced:
            if not in_region:
                in_region = True
                region_start_fi = fi
                voiced_run = 1
            else:
                voiced_run += 1

            if voiced_run == min_consecutive_voiced_frames:
                t = region_start_fi * (frame_ms / 1000.0)
                candidates.append(t)
                if len(candidates) >= max_candidates:
                    break
        else:
            in_region = False
            voiced_run = 0

    return candidates


def _moving_average(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1:
        return x
    # pad to keep same length
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
    thresh_db_above_floor: float = 12.0,
    min_sustain_ms: float = 350.0,
) -> List[float]:
    """
    Singing-friendly candidate detector.

    Approach:
    - compute abs-envelope
    - smooth it
    - convert to dB
    - find first sustained region above (noise floor + thresh)

    This catches vocal entrances even when VAD returns nothing.
    """
    max_samples = int(min(len(audio_16k), max_scan_secs * sr))
    if max_samples <= 0:
        return []

    x = audio_16k[:max_samples]
    env = np.abs(x).astype(np.float32)

    hop = max(1, int(sr * hop_ms / 1000.0))
    # downsample envelope to hop rate
    env_h = env[::hop]
    smooth_win = max(1, int((smooth_ms / hop_ms)))
    env_s = _moving_average(env_h, smooth_win)

    eps = 1e-6
    db = 20.0 * np.log10(env_s + eps)

    # noise floor estimate: lower percentile over first ~60s or entire scan window if shorter
    scan_len = len(db)
    first_n = int(min(scan_len, (60.0 / (hop_ms / 1000.0))))
    floor = float(np.percentile(db[:first_n] if first_n > 10 else db, 20.0))
    thresh = floor + float(thresh_db_above_floor)

    sustain_frames = max(1, int(min_sustain_ms / hop_ms))

    candidates: List[float] = []
    run = 0
    in_region = False
    region_start_i = 0

    for i in range(scan_len):
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
    avg_logprob = getattr(seg, "avg_logprob", None)
    no_speech_prob = getattr(seg, "no_speech_prob", None)
    wprob = getattr(w, "probability", None)

    if no_speech_prob is not None and no_speech_prob > 0.60:
        return False
    if avg_logprob is not None and avg_logprob < -1.20:
        return False
    if wprob is not None and wprob < 0.20:
        return False

    word = (getattr(w, "word", "") or "").strip()
    if len(word) <= 1:
        return False
    return True


def _merge_candidates(*lists: List[float], max_total: int = 8) -> List[float]:
    seen = set()
    out: List[float] = []
    for lst in lists:
        for t in lst:
            # quantize to 0.1s for dedupe
            q = round(float(t), 1)
            if q in seen:
                continue
            seen.add(q)
            out.append(float(t))
            if len(out) >= max_total:
                return out
    return out


def estimate_first_word_time(
    audio_path: str,
    *,
    model_size: str = "tiny",
    device: str = "cpu",
    compute_type: str = "int8",
    language: Optional[str] = None,
    max_scan_secs: float = 300.0,
    pre_roll_secs: float = 1.5,
    window_secs: float = 12.0,
    detector: str = "auto",   # auto|vad|energy
    vad_bandpass: bool = True,
    max_whisper_windows: int = 5,
    verbose: bool = False,
) -> Optional[FirstWordResult]:
    audio_16k = _ffmpeg_decode_s16le_16k_mono(audio_path, bandpass=vad_bandpass)

    detector = detector.lower().strip()
    if detector not in ("auto", "vad", "energy"):
        raise ValueError("detector must be one of: auto, vad, energy")

    vad_cands: List[float] = []
    eng_cands: List[float] = []

    if detector in ("auto", "vad"):
        vad_cands = _find_voiced_candidates_webrtcvad(
            audio_16k,
            frame_ms=30,
            vad_mode=3,
            min_consecutive_voiced_frames=12,
            max_scan_secs=max_scan_secs,
            max_candidates=6,
        )

    if detector in ("auto", "energy"):
        # If VAD found nothing (common for singing), energy detector is the fallback
        eng_cands = _find_voiced_candidates_energy(
            audio_16k,
            max_scan_secs=max_scan_secs,
            max_candidates=6,
            thresh_db_above_floor=12.0,
            min_sustain_ms=350.0,
        )

    # In auto mode, prefer VAD candidates first (if any), then energy candidates
    candidates = _merge_candidates(vad_cands, eng_cands, max_total=8)

    if verbose:
        print(f"[DEBUG] vad_candidates={['%.2f' % c for c in vad_cands]}")
        print(f"[DEBUG] energy_candidates={['%.2f' % c for c in eng_cands]}")
        print(f"[DEBUG] merged_candidates={['%.2f' % c for c in candidates]}")

    if not candidates:
        return None

    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    tried = 0
    for t0 in candidates:
        clip_start = max(0.0, float(t0) - float(pre_roll_secs))
        clip, _sr = _ffmpeg_extract_s16le_16k_mono(audio_path, clip_start, window_secs)

        segments, _info = model.transcribe(
            clip,
            language=language,
            beam_size=1,                   # greedy, fast
            vad_filter=False,              # already trimmed
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
    ap.add_argument("--detector", default="auto", help="Candidate detector: auto|vad|energy (default auto).")
    ap.add_argument("--max-scan-secs", type=float, default=300.0, help="How far into the track to scan.")
    ap.add_argument("--pre-roll", type=float, default=1.5, help="Seconds before candidate time to include in the Whisper window.")
    ap.add_argument("--window", type=float, default=12.0, help="Whisper window length in seconds.")
    ap.add_argument("--max-windows", type=int, default=5, help="Maximum Whisper windows to try (keeps compute low).")
    ap.add_argument("--no-bandpass", action="store_true", help="Disable band-pass filtering for detectors.")
    ap.add_argument("--verbose", action="store_true", help="Print debug info.")
    args = ap.parse_args()

    res = estimate_first_word_time(
        args.audio,
        model_size=args.model,
        language=args.language,
        detector=args.detector,
        max_scan_secs=args.max_scan_secs,
        pre_roll_secs=args.pre_roll,
        window_secs=args.window,
        vad_bandpass=not args.no_bandpass,
        max_whisper_windows=args.max_windows,
        verbose=args.verbose,
    )

    if res is None:
        print("No first-word time detected (detectors produced no candidates or Whisper rejected all).")
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
