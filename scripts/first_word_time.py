#!/usr/bin/env python3
"""
Estimate the time (seconds) of the very first *word* in a song with minimal Whisper compute.

Strategy (fast):
1) Run ultra-fast WebRTC VAD on band-passed audio to find the first likely "voiced" frame.
2) Transcribe ONLY a small window around that time using faster-whisper (tiny) and return the
   timestamp of the first decoded word (roughly).

Requirements:
  - ffmpeg installed and on PATH
  - pip3 install faster-whisper webrtcvad numpy

Notes:
- This is tuned for speed, not perfect accuracy on every mix.
- If you already have a vocals stem (Demucs), pass it as --audio for best results.
"""

import argparse
import math
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple, List

import numpy as np

try:
    import webrtcvad
except ImportError as e:
    raise SystemExit(
        "Missing dependency webrtcvad. Install with:\n"
        "  pip3 install webrtcvad\n"
        f"Original error: {e}"
    )

try:
    from faster_whisper import WhisperModel
except ImportError as e:
    raise SystemExit(
        "Missing dependency faster-whisper. Install with:\n"
        "  pip3 install faster-whisper\n"
        f"Original error: {e}"
    )


@dataclass
class FirstWordResult:
    first_word_time_secs: float
    first_word: str
    confidence: Optional[float] = None  # best-effort; may be None


def _ffmpeg_decode_s16le_16k_mono(audio_path: str, bandpass: bool = True) -> np.ndarray:
    """
    Decode audio to mono 16kHz int16 PCM using ffmpeg, optionally band-pass filtering
    to help VAD focus on voice-like frequencies.
    Returns float32 in [-1, 1].
    """
    af = []
    if bandpass:
        # Speech-ish band; helps VAD ignore sub-bass and very high cymbals.
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
    audio = pcm16.astype(np.float32) / 32768.0
    return audio


def _find_first_voiced_time_webrtcvad(
    audio_16k: np.ndarray,
    *,
    frame_ms: int = 30,
    vad_mode: int = 2,
    min_consecutive_voiced_frames: int = 6,
    max_scan_secs: float = 120.0,
) -> Optional[float]:
    """
    Run WebRTC VAD over the first max_scan_secs and return the earliest time where
    min_consecutive_voiced_frames occur.
    """
    if frame_ms not in (10, 20, 30):
        raise ValueError("frame_ms must be 10, 20, or 30")

    vad = webrtcvad.Vad(vad_mode)
    sample_rate = 16000
    frame_len = int(sample_rate * frame_ms / 1000)  # samples per frame
    max_samples = int(min(len(audio_16k), max_scan_secs * sample_rate))

    # Convert to int16 bytes required by webrtcvad
    pcm16 = (np.clip(audio_16k[:max_samples], -1.0, 1.0) * 32767.0).astype(np.int16)
    raw = pcm16.tobytes()

    voiced_run = 0
    for i in range(0, len(pcm16) - frame_len + 1, frame_len):
        frame = raw[i * 2 : (i + frame_len) * 2]  # int16 => 2 bytes each
        is_voiced = vad.is_speech(frame, sample_rate)

        if is_voiced:
            voiced_run += 1
            if voiced_run >= min_consecutive_voiced_frames:
                # Return the time at the start of the run (approx)
                start_frame_index = (i // frame_len) - (min_consecutive_voiced_frames - 1)
                return start_frame_index * (frame_ms / 1000.0)
        else:
            voiced_run = 0

    return None


def _ffmpeg_extract_wav_segment(
    audio_path: str,
    start_sec: float,
    duration_sec: float,
) -> Tuple[np.ndarray, int]:
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
    audio = pcm16.astype(np.float32) / 32768.0
    return audio, 16000


def estimate_first_word_time(
    audio_path: str,
    *,
    model_size: str = "tiny",
    device: str = "cpu",
    compute_type: str = "int8",
    max_scan_secs: float = 120.0,
    pre_roll_secs: float = 0.8,
    window_secs: float = 10.0,
    vad_bandpass: bool = True,
) -> Optional[FirstWordResult]:
    """
    Returns the time (seconds) of the first decoded word, roughly.

    Whisper compute is minimized by:
    - VAD scanning without Whisper (fast)
    - Whisper only on a short window around the first voiced time

    If nothing is found, returns None.
    """
    # 1) Fast VAD scan
    audio_16k = _ffmpeg_decode_s16le_16k_mono(audio_path, bandpass=vad_bandpass)
    t_voiced = _find_first_voiced_time_webrtcvad(
        audio_16k,
        frame_ms=30,
        vad_mode=2,
        min_consecutive_voiced_frames=6,
        max_scan_secs=max_scan_secs,
    )
    if t_voiced is None:
        return None

    # 2) Extract short window around VAD hit
    clip_start = max(0.0, t_voiced - pre_roll_secs)
    clip, sr = _ffmpeg_extract_wav_segment(audio_path, clip_start, window_secs)

    # 3) Minimal Whisper: tiny + greedy decode + word timestamps on this short clip
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    segments, info = model.transcribe(
        clip,
        language="en",  # set None to auto-detect; setting helps speed/stability
        beam_size=1,    # greedy
        vad_filter=False,  # already trimmed
        word_timestamps=True,
        condition_on_previous_text=False,
        temperature=0.0,
    )

    # Find first word across segments
    for seg in segments:
        if not seg.words:
            continue
        w0 = seg.words[0]
        # w0.start is relative to clip start
        if w0.start is None:
            # fallback to segment start
            return FirstWordResult(
                first_word_time_secs=clip_start + float(seg.start),
                first_word=(seg.text or "").strip().split(" ")[0] if seg.text else "",
                confidence=None,
            )
        return FirstWordResult(
            first_word_time_secs=clip_start + float(w0.start),
            first_word=(w0.word or "").strip(),
            confidence=getattr(w0, "probability", None),
        )

    # If whisper produced segments but no words, fall back to first segment start if possible
    # (segments is an iterator; re-run fast without word timestamps as last resort)
    segments2, _info2 = model.transcribe(
        clip,
        language="en",
        beam_size=1,
        vad_filter=False,
        word_timestamps=False,
        condition_on_previous_text=False,
        temperature=0.0,
    )
    for seg in segments2:
        txt = (seg.text or "").strip()
        if txt:
            return FirstWordResult(
                first_word_time_secs=clip_start + float(seg.start),
                first_word=txt.split(" ")[0],
                confidence=None,
            )

    return None


def _fmt_mmss(seconds: float) -> str:
    m = int(seconds // 60)
    s = seconds - (m * 60)
    return f"{m:02d}:{s:05.2f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="Path to audio file (mp3/wav/etc). Prefer vocals stem if available.")
    ap.add_argument("--model", default="tiny", help="faster-whisper model size (tiny/base/small/...). Default tiny for speed.")
    ap.add_argument("--max-scan-secs", type=float, default=120.0, help="How far into the song to scan with VAD.")
    ap.add_argument("--pre-roll", type=float, default=0.8, help="Seconds before detected voice to include in Whisper window.")
    ap.add_argument("--window", type=float, default=10.0, help="Whisper window length in seconds.")
    ap.add_argument("--no-bandpass", action="store_true", help="Disable band-pass filtering for VAD.")
    args = ap.parse_args()

    res = estimate_first_word_time(
        args.audio,
        model_size=args.model,
        max_scan_secs=args.max_scan_secs,
        pre_roll_secs=args.pre_roll,
        window_secs=args.window,
        vad_bandpass=not args.no_bandpass,
    )

    if res is None:
        print("No first-word time detected (VAD or Whisper returned nothing).")
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
