#!/usr/bin/env python3
# experimental/5gen_mp4.py
# Final MP4 generation: builds text overlay (-vf) and applies global audio offset (-af)

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from ffmpeg_helpers import build_audio_offset_filter

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MP4_DIR = BASE_DIR / "mp4s"

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
    """
    Looks for:
      offsets/{slug}.json  keys: offset_seconds | offset | seconds | ms
      offsets/{slug}.txt   single float seconds
    Defaults to 0.0
    """
    j = OFFSETS_DIR / f"{slug}.json"
    if j.exists():
        try:
            data = json.loads(j.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "ms" in data:
                    return float(data["ms"]) / 1000.0
                for k in ("offset_seconds", "offset", "seconds"):
                    if k in data:
                        return float(data[k])
        except Exception:
            pass
    t = OFFSETS_DIR / f"{slug}.txt"
    if t.exists():
        try:
            return float(t.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    return 0.0


def build_drawtext_filters(events: list[dict], offset: float) -> str:
    """Build drawtext chain from timing events plus a global offset (sec)."""
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
            next_time = next((events[j]["time"] for j in main_indices if events[j]["time"] > t), None)
            end = min(start + LINE_DURATION_DEFAULT, (next_time + offset - 0.1) if next_time is not None else start + LINE_DURATION_DEFAULT)
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

    return ",".join(filters) if filters else "null"


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

    MP4_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MP4_DIR / f"{slug}.mp4"

    if SAFE_REGEN and out_path.exists():
        log("MP4", f"Output already exists, skipping: {out_path}", YELLOW)
        return

    events = load_timings(slug)
    offset = load_offset(slug)
    vf_chain = build_drawtext_filters(events, offset)
    af_chain = build_audio_offset_filter(offset)  # "" if no-op

    # DEBUG: print exactly what we will pass to ffmpeg (helps catch stray numbers)
    log("MP4", f"offset={offset:+.3f}s  af_chain={'<none>' if not af_chain else af_chain}", GREEN)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f", "lavfi",
        "-i", f"color=size={WIDTH}x{HEIGHT}:rate={FPS}:color=black",
        "-i", str(audio_path),
        "-vf", vf_chain,
    ]

    # Only add -af if we have a real filter (never a bare number)
    if af_chain:
        cmd += ["-af", af_chain]

    cmd += [
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "fast",
        "-c:a", "aac",
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

# end of 5gen_mp4.py
