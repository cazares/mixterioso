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
META_DIR = PROJECT_ROOT / "meta"
MP4_DIR = PROJECT_ROOT / "mp4s"

# Visual constants – keep close to what you already had
VIDEO_SIZE = "1280x720"
VIDEO_FPS = 30
FONTFILE = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
LYRIC_FONT_SIZE = 64
NOTE_FONT_SIZE = 48
LYRIC_Y = "h-160"
NOTE_Y = "h-220"
BORDER_COLOR = "black"
BORDER_W = 2

# Durations
LYRIC_DURATION = 2.5   # seconds each lyric line is on screen
NOTE_DURATION = 1.5    # seconds for glyph events
INTRO_MIN_DURATION = 3.0  # minimum intro duration if first event is very late


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
    out = subprocess.check_output(cmd, text=True).strip()
    try:
        return float(out)
    except ValueError:
        raise SystemExit(f"Could not parse duration from ffprobe output: {out!r}")


def load_timings(slug: str) -> tuple[list[dict], Path]:
    timing_path = TIMING_DIR / f"{slug}.csv"
    if not timing_path.exists():
        log("MP4", f"Timings CSV not found: {timing_path}", RED)
        sys.exit(1)

    events: list[dict] = []
    with timing_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "")
            try:
                idx = int(row.get("line_index", -1) or -1)
            except ValueError:
                idx = -1
            events.append({"time": t, "text": text, "line_index": idx})
    events.sort(key=lambda e: e["time"])
    if not events:
        log("MP4", f"No timing rows in {timing_path}", RED)
        sys.exit(1)
    return events, timing_path


def load_offset(slug: str) -> tuple[float, Path]:
    OFFSET_DIR.mkdir(parents=True, exist_ok=True)
    offset_path = OFFSET_DIR / f"{slug}.json"
    if not offset_path.exists():
        log("MP4", f"No offset JSON, using 0.000s", YELLOW)
        return 0.0, offset_path
    try:
        data = json.loads(offset_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "offset_secs" in data:
                v = float(data["offset_secs"])
            elif "offset" in data:
                v = float(data["offset"])
            else:
                v = 0.0
        elif isinstance(data, (int, float)):
            v = float(data)
        else:
            v = 0.0
        log("MP4", f"Using offset {v:+.3f}s for slug {slug}", GREEN)
        return v, offset_path
    except Exception as e:
        log("MP4", f"Failed to parse offset JSON {offset_path}: {e}", YELLOW)
        return 0.0, offset_path


def load_title(slug: str) -> str:
    meta_path = META_DIR / f"{slug}.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            artist = data.get("artist") or ""
            title = data.get("title") or slug
            if artist:
                return f"{artist} – {title}"
            return title
        except Exception:
            pass
    # Fallback: nice slug
    return slug.replace("_", " ").title()


def escape_drawtext_text(s: str) -> str:
    """
    Escape text for ffmpeg drawtext when using text="...".
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace(":", "\\:")
    s = s.replace(",", "\\,")
    s = s.replace("'", "\\'")
    return s


def build_drawtext_filter(
    text: str,
    start: float,
    end: float,
    is_note: bool = False,
) -> str:
    esc = escape_drawtext_text(text)
    fontsize = NOTE_FONT_SIZE if is_note else LYRIC_FONT_SIZE
    y_pos = NOTE_Y if is_note else LYRIC_Y
    return (
        f"drawtext=fontfile='{FONTFILE}':"
        f"text=\"{esc}\":"
        f"fontsize={fontsize}:"
        f"fontcolor=white:"
        f"bordercolor={BORDER_COLOR}:"
        f"borderw={BORDER_W}:"
        f"x=(w-text_w)/2:"
        f"y={y_pos}:"
        f"enable='between(t,{start:.3f},{end:.3f})'"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate final MP4 with lyrics (step 5).")
    p.add_argument("slug", help="Song slug, e.g. under_the_bridge")
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

    MP4_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MP4_DIR / f"{slug}.mp4"

    # Load timings and offset
    events, timing_path = load_timings(slug)
    offset, _ = load_offset(slug)

    # Audio duration
    duration = ffprobe_duration(mp3_path)
    log("MP4", f"Audio duration: {fmt_time(duration)}", CYAN)

    # Build filter graph
    title = load_title(slug)

    # First event start (with offset applied) for intro screen
    first_event_time = events[0]["time"] + offset
    if first_event_time < 0.0:
        first_event_time = 0.0
    intro_end = max(first_event_time, INTRO_MIN_DURATION)

    filters: list[str] = []

    # Intro title, from t=0 to intro_end
    filters.append(
        build_drawtext_filter(
            text=title,
            start=0.0,
            end=intro_end,
            is_note=False,
        )
    )

    # Per-event overlays
    for ev in events:
        base_t = ev["time"] + offset
        if base_t < 0.0:
            start = 0.0
        else:
            start = base_t

        is_note = ev["line_index"] < 0
        dur = NOTE_DURATION if is_note else LYRIC_DURATION
        end = start + dur

        # Clamp to audio duration
        if start >= duration:
            continue
        if end > duration:
            end = duration

        if end <= start:
            continue

        filters.append(
            build_drawtext_filter(
                text=ev["text"],
                start=start,
                end=end,
                is_note=is_note,
            )
        )

    if not filters:
        log("MP4", "No filters produced (this should not happen).", RED)
        sys.exit(1)

    filter_complex = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=size={VIDEO_SIZE}:rate={VIDEO_FPS}:color=black",
        "-i",
        str(mp3_path),
        "-filter_complex",
        filter_complex,
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

    log("MP4", " ".join(cmd), CYAN)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log("MP4", f"ffmpeg failed with code {e.returncode}", RED)
        sys.exit(e.returncode)

    log("MP4", f"MP4 written to {out_path}", GREEN)


if __name__ == "__main__":
    main()

# end of 5gen_mp4.py
