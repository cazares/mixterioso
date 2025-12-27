#!/usr/bin/env python3
"""Step 4: Render karaoke MP4.

Inputs:
- mixes/<slug>.(wav|mp3) OR mp3s/<slug>.mp3 (fallback)
- timings/<slug>.csv (canonical)

Outputs:
- output/<slug>.mp4

Burns subtitles into video with ffmpeg.
"""

from __future__ import annotations

import csv
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .common import IOFlags, Paths, log, run_cmd, should_write, write_text


def _escape_subtitles_path(p: Path) -> str:
    # Escape for ffmpeg subtitles filter (libass).
    # - Backslashes must be doubled
    # - Colons must be escaped (Windows drive letters / filter syntax)
    # - Single quotes must be escaped if we quote with '...'
    s = str(p)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    return s


def _read_csv(csv_path: Path) -> List[Tuple[float, str]]:
    rows: List[Tuple[float, str]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                t = float(row.get("time_secs", ""))
            except Exception:
                continue
            txt = (row.get("text") or "").strip()
            if txt:
                rows.append((t, txt))
    rows.sort(key=lambda x: x[0])
    return rows


def _sec_to_srt(ts: float) -> str:
    if ts < 0:
        ts = 0.0
    ms = int(round(ts * 1000.0))
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _probe_duration_secs(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out)
    except Exception:
        return None


def _pick_audio(paths: Paths, slug: str) -> Path:
    for p in [paths.mixes / f"{slug}.wav", paths.mixes / f"{slug}.mp3", paths.mp3s / f"{slug}.mp3"]:
        if p.exists():
            return p
    raise FileNotFoundError(f"No audio found for slug={slug} (expected mixes/ or mp3s/)")


def step4_build(paths: Paths, *, slug: str, offset: float, flags: IOFlags) -> Path:
    csv_path = paths.timings / f"{slug}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing timings CSV: {csv_path}")

    audio_path = _pick_audio(paths, slug)
    out_path = paths.output / f"{slug}.mp4"
    srt_path = paths.cache / f"{slug}.srt"

    if out_path.exists() and not should_write(out_path, flags, label="mp4"):
        log("MP4", f"Reusing existing video: {out_path}")
        return out_path

    rows = _read_csv(csv_path)
    if not rows:
        raise RuntimeError(f"Timings CSV has no usable rows: {csv_path}")

    dur = _probe_duration_secs(audio_path) or (rows[-1][0] + 10.0)

    # Build SRT using next line time as end, clamp to duration.
    srt_lines: List[str] = []
    for i, (t, txt) in enumerate(rows, 1):
        start = t + offset
        end = (rows[i][0] + offset) if i < len(rows) else (dur + offset)
        if end <= start:
            end = start + 2.0
        # clamp
        if start < 0:
            start = 0.0
        if end < 0:
            end = 0.5
        srt_lines.append(str(i))
        srt_lines.append(f"{_sec_to_srt(start)} --> {_sec_to_srt(min(end, dur))}")
        srt_lines.append(txt)
        srt_lines.append("")

    write_text(srt_path, "\n".join(srt_lines), flags, label="srt")

    # Make black video and burn subtitles.
    # Keep things simple and portable.
    paths.output.mkdir(parents=True, exist_ok=True) if not flags.dry_run else None

    # libass filter expects a path string; avoid ':' escaping issues by passing as-is on POSIX.
    vf = f"subtitles=\'{_escape_subtitles_path(srt_path)}\'"
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=1920x1080:r=30:d={dur}",
        "-i",
        str(audio_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(out_path),
    ]

    rc = run_cmd(cmd, tag="FFMPEG", dry_run=flags.dry_run)
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc})")

    log("MP4", f"Wrote {out_path}", color="\033[32m")
    return out_path


# end of step4_build.py
