#!/usr/bin/env python3
# audio_choice.py - Determines best source (MP3 vs MP4) for Whisper-based analysis
# Full version (no omissions)

import os
from pathlib import Path
import librosa

ROOT = Path(__file__).resolve().parent.parent
MP3_DIR = ROOT / "mp3s"
OUTPUT_DIR = ROOT / "output"


def choose_audio_source(slug: str):
    """Return (mp3_path, mp4_path) and pick preferred audio automatically."""
    mp3_path = MP3_DIR / f"{slug}.mp3"
    mp4_path = OUTPUT_DIR / f"{slug}.mp4"

    mp3_exists = mp3_path.exists()
    mp4_exists = mp4_path.exists()

    if not mp3_exists and not mp4_exists:
        print(f"[AUTO_OFFSET] No audio found for {slug}")
        return None, None

    mp3_dur = mp4_dur = 0.0
    try:
        if mp3_exists:
            mp3_dur = librosa.get_duration(path=mp3_path)
        if mp4_exists:
            mp4_dur = librosa.get_duration(path=mp4_path)
    except Exception:
        pass

    diff = abs(mp3_dur - mp4_dur)
    if mp4_exists and (diff < 0.5):
        print(f"[AUTO_OFFSET] Using MP4 ({mp4_dur:.1f}s) for quality")
        return mp3_path if mp3_exists else None, mp4_path
    elif mp3_exists:
        print(f"[AUTO_OFFSET] Using MP3 ({mp3_dur:.1f}s) for speed")
        return mp3_path, mp4_path if mp4_exists else None
    else:
        return None, mp4_path


if __name__ == "__main__":
    print(choose_audio_source("test"))

# end of audio_choice.py
