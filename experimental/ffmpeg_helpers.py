#!/usr/bin/env python3
# experimental/ffmpeg_helpers.py
# Purpose: safe builders for ffmpeg filter strings

from __future__ import annotations

def build_audio_offset_filter(offset_s: float | int | str) -> str:
    """
    Returns a valid -af filter that shifts audio by offset_s seconds.
    > 0  : delay audio by N seconds (pad with silence)
    < 0  : cut N seconds from start and re-timestamp
    == 0 : no filter
    """
    try:
        s = float(offset_s)
    except (TypeError, ValueError):
        s = 0.0

    if abs(s) < 1e-9:
        return ""

    if s > 0:
        # adelay uses milliseconds per channel
        ms = int(round(s * 1000.0))
        return f"adelay={ms}|{ms}:all=1"
    else:
        cut = abs(s)
        # Trim from start then reset timestamps
        return f"atrim=start={cut},asetpts=PTS-STARTPTS"
# end of ffmpeg_helpers.py
