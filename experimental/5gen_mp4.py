#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

MP3_DIR = PROJECT_ROOT / "mp3s"
TIMING_DIR = PROJECT_ROOT / "timings"
OFFSET_DIR = PROJECT_ROOT / "offsets"
MP4_DIR = PROJECT_ROOT / "mp4s"

VIDEO_SIZE = "1280x720"
VIDEO_RATE = 30
BG_COLOR = "black"

# macOS font path you used before
DEFAULT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

LYRIC_FONT_SIZE = 64
NOTE_FONT_SIZE = 48

LYRIC_Y = "h-160"
NOTE_Y = "h-220"

LYRIC_DURATION = 2.5
NOTE_DURATION = 1.5


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    rem = sec - m * 60
    s = int(rem)
    ms = int(round((rem - s) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    if s == 60:
        m += 1
        s = 0
    return f"{m:02d}:{s:02d}.{ms:03d}"


def ffprobe_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        raise SystemExit(f"Could not parse duration from ffprobe output: {out!r}")


def escape_drawtext_text(text: str) -> str:
    """
    Escape text for ffmpeg drawtext:
    - backslash
    - colon
    - comma
    - double quote

    We deliberately do NOT escape single quotes here since we wrap in double quotes.
    """
    s = text
    s = s.replace("\\", r"\\")
    s = s.replace(":", r"\:")
    s = s.replace(",", r"\,")
    s = s.replace('"', r'\"')
    return s


def load_timings(slug: str) -> tuple[list[dict], Path]:
    timing_path = TIMING_DIR / f"{slug}.csv"
    if not timing_path.exists():
        return [], timing_path

    events: list[dict] = []
    with timing_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "")
            if not text:
                continue
            idx = int(row.get("line_index", -1) or -1)
            events.append({"time": t, "text": text, "line_index": idx})
    events.sort(key=lambda e: e["time"])
    return events, timing_path


def load_offset(slug: str) -> tuple[float, Path]:
    OFFSET_DIR.mkdir(parents=True, exist_ok=True)
    offset_path = OFFSET_DIR / f"{slug}.json"
    if not offset_path.exists():
        return 0.0, offset_path
    try:
        data = json.loads(offset_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "offset_secs" in data:
                return float(data["offset_secs"]), offset_path
            if "offset" in data:
                return float(data["offset"]), offset_path
        if isinstance(data, (int, float)):
            return float(data), offset_path
    except Exception:
        pass
    return 0.0, offset_path


def build_filter_complex(events: list[dict], offset: float, fontfile: str) -> str:
    pieces: list[str] = []

    for ev in events:
        t_base = ev["time"] + offset
        if t_base < 0:
            # clamp to t=0 so it at least appears
            t_base = 0.0

        text_escaped = escape_drawtext_text(ev["text"])

        if ev["line_index"] < 0:
            # note / glyph event
            fontsize = NOTE_FONT_SIZE
            y_expr = NOTE_Y
            duration = NOTE_DURATION
        else:
            fontsize = LYRIC_FONT_SIZE
            y_expr = LYRIC_Y
            duration = LYRIC_DURATION

        start = t_base
        end = t_base + duration

        # text is wrapped in double quotes; enable expression uses single quotes
        piece = (
            f"drawtext=fontfile='{fontfile}':"
            f'text="{text_escaped}":'
            f"fontsize={fontsize}:fontcolor=white:bordercolor=black:borderw=2:"
            f"x=(w-text_w)/2:y={y_expr}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
        pieces.append(piece)

    return ",".join(pieces)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate MP4 with lyrics (step 5).")
    p.add_argument(
        "slug",
        help="Song slug, e.g. cant_stop",
    )
    p.add_argument(
        "--font",
        default=DEFAULT_FONT,
        help=f"Font file for drawtext (default: {DEFAULT_FONT})",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = args.slug.strip()
    if not slug:
        raise SystemExit("Slug is required.")

    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log("MP4", f"Audio not found: {mp3_path}", RED)
        sys.exit(1)

    events, timing_path = load_timings(slug)
    if not timing_path.exists():
        log("MP4", f"Timings CSV not found: {timing_path}", RED)
        sys.exit(1)
    if not events:
        log("MP4", f"No timing events in {timing_path}, nothing to render.", YELLOW)
        sys.exit(1)

    offset, offset_path = load_offset(slug)
    log("MP4", f"Using offset {offset:+.3f}s for slug {slug}", CYAN)
    if not offset_path.exists():
        log("MP4", "No offset file found, using 0.000s.", YELLOW)

    duration = ffprobe_duration(mp3_path)
    log("MP4", f"Audio duration: {fmt_time(duration)}", GREEN)

    filter_complex = build_filter_complex(events, offset, args.font)

    if not filter_complex:
        log("MP4", "Filter graph is empty; no text will be drawn.", YELLOW)

    MP4_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MP4_DIR / f"{slug}.mp4"

    color_src = f"color=size={VIDEO_SIZE}:rate={VIDEO_RATE}:color={BG_COLOR}"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        color_src,
        "-i",
        str(mp3_path),
    ]

    if filter_complex:
        cmd.extend(["-filter_complex", filter_complex])

    cmd.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            str(out_path),
        ]
    )

    log("MP4", " ".join(cmd), CYAN)
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        log("MP4", f"ffmpeg failed with code {proc.returncode}", RED)
        sys.exit(proc.returncode)

    log("MP4", f"MP4 written to {out_path}", GREEN)


if __name__ == "__main__":
    main()

# end of 5gen_mp4.py
