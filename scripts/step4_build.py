#!/usr/bin/env python3
import csv, subprocess
from pathlib import Path
from .common import IOFlags, Paths, log, run_cmd, should_write, write_text

VIDEO_WIDTH, VIDEO_HEIGHT = 854, 480
FPS = 5

def step4_build(paths: Paths, *, slug: str, offset: float, flags: IOFlags) -> Path:
    csv_path = paths.timings / f"{slug}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing timings CSV: {csv_path}")

    audio_path = None
    for p in [paths.mixes / f"{slug}.wav", paths.mixes / f"{slug}.mp3", paths.mp3s / f"{slug}.mp3"]:
        if p.exists():
            audio_path = p
            break
    if not audio_path:
        raise FileNotFoundError(f"No audio found for slug={slug}")

    out_path = paths.output / f"{slug}.mp4"
    srt_path = paths.cache / f"{slug}.srt"
    if out_path.exists() and not should_write(out_path, flags, label="mp4"):
        log("MP4", f"Reusing existing video: {out_path}")
        return out_path

    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                t = float(row.get("time_secs", ""))
            except Exception:
                continue
            txt = (row.get("text") or "").strip()
            if txt:
                rows.append((t, txt))
    if not rows:
        raise RuntimeError(f"Timings CSV has no usable rows: {csv_path}")

    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        text=True).strip() or 0) or (rows[-1][0] + 10.0)

    def _sec_to_srt(ts: float) -> str:
        if ts < 0: ts = 0.0
        ms = int(round(ts * 1000))
        s, ms = divmod(ms, 1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    srt_lines = []
    for i, (t, txt) in enumerate(rows, 1):
        start = t + offset
        end = (rows[i][0] + offset) if i < len(rows) else (dur + offset)
        if end <= start: end = start + 2.0
        start, end = max(0, start), max(0.5, end)
        srt_lines += [str(i),
                      f"{_sec_to_srt(start)} --> {_sec_to_srt(min(end, dur))}",
                      txt, ""]
    write_text(srt_path, "\n".join(srt_lines), flags, label="srt")

    vf = f"subtitles='{srt_path}'"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "lavfi", "-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r={FPS}:d={dur}",
        "-i", str(audio_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(out_path)
    ]
    rc = run_cmd(cmd, tag="FFMPEG", dry_run=flags.dry_run)
    if rc != 0:
        raise RuntimeError(f"ffmpeg failed ({rc})")
    log("MP4", f"Wrote {out_path}")
    return out_path
# end of step4_build.py
