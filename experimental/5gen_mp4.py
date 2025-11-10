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

BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
OUTPUT_DIR = BASE_DIR / "output"

WIDTH = 1280
HEIGHT = 720
FPS = 30
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"  # adjust if needed
MAIN_FONT_SIZE = 64
NOTE_FONT_SIZE = 48
LINE_DURATION_DEFAULT = 2.5  # seconds
SAFE_REGEN = True  # skip ffmpeg if final mp4 exists


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def load_timings(slug: str) -> list[dict]:
    path = TIMINGS_DIR / f"{slug}.csv"
    if not path.exists():
        raise SystemExit(f"Timings CSV not found: {path}")
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row["line_index"])
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "") or ""
            events.append({"line_index": idx, "time": t, "text": text})
    events.sort(key=lambda e: e["time"])
    return events


def load_offset(slug: str) -> float:
    path = OFFSETS_DIR / f"{slug}.json"
    if not path.exists():
        return 0.0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("offset", 0.0))
    except Exception:
        log("MP4", f"Failed to parse offset file {path}, using 0.0", YELLOW)
        return 0.0


def build_drawtext_filters(events: list[dict], offset: float) -> str:
    """
    Build a drawtext chain from timing events and a global offset.
    CSV remains intact; we only shift times here.
    """
    if not events:
        return "null"

    main_indices = [i for i, e in enumerate(events) if e["line_index"] >= 0]
    filters: list[str] = []

    for i, ev in enumerate(events):
        t = float(ev["time"])
        text = ev["text"]
        line_index = ev["line_index"]

        start = t + offset

        if line_index >= 0:
            next_time = None
            for j in main_indices:
                if events[j]["time"] > t:
                    next_time = events[j]["time"]
                    break
            if next_time is not None:
                end = min(start + LINE_DURATION_DEFAULT, next_time + offset - 0.1)
            else:
                end = start + LINE_DURATION_DEFAULT
            fontsize = MAIN_FONT_SIZE
            y_expr = "h-160"
        else:
            end = start + 1.5
            fontsize = NOTE_FONT_SIZE
            y_expr = "h-220"

        if end <= 0:
            continue
        start_clamped = max(start, 0.0)

        safe_text = (
            text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\'")
        )

        draw = (
            f"drawtext=fontfile='{FONT_PATH}':"
            f"text='{safe_text}':"
            f"fontsize={fontsize}:fontcolor=white:bordercolor=black:borderw=2:"
            f"x=(w-text_w)/2:y={y_expr}:"
            f"enable='between(t,{start_clamped:.3f},{end:.3f})'"
        )
        filters.append(draw)

    if not filters:
        return "null"
    return ",".join(filters)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate final karaoke MP4.")
    p.add_argument("slug", help="Song slug (e.g. 'californication')")
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    slug = slugify(args.slug)

    audio_path = MP3_DIR / f"{slug}.mp3"
    if not audio_path.exists():
        raise SystemExit(f"Audio not found: {audio_path}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{slug}.mp4"

    if SAFE_REGEN and out_path.exists():
        log("MP4", f"Output already exists, skipping: {out_path}", YELLOW)
        return

    events = load_timings(slug)
    offset = load_offset(slug)
    log("MP4", f"Using offset {offset:+.3f}s for slug {slug}", GREEN)

    filter_complex = build_drawtext_filters(events, offset)

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=size={WIDTH}x{HEIGHT}:rate={FPS}:color=black",
        "-i",
        str(audio_path),
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

    log("MP4", f"Final video saved to {out_path}", GREEN)


if __name__ == "__main__":
    main()

# end of 5_gen_mp4.py
