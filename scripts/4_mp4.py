#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
import os

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

BOTTOM_BOX_HEIGHT_FRACTION = 0.20
TOP_BAND_FRACTION = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION
NEXT_LYRIC_TOP_MARGIN_PX = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50
DIVIDER_LINE_OFFSET_UP_PX = 0
DIVIDER_HEIGHT_PX = 0.25
DIVIDER_LEFT_MARGIN_PX = VIDEO_WIDTH * 0.035
DIVIDER_RIGHT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX
VERTICAL_OFFSET_FRACTION = 0.0
TITLE_EXTRA_OFFSET_FRACTION = -0.20
NEXT_LINE_FONT_SCALE = 0.475
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.55
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX
FADE_IN_MS = 50
FADE_OUT_MS = 50

GLOBAL_NEXT_COLOR_RGB = "FFFFFF"
GLOBAL_NEXT_ALPHA_HEX = "4D"
DIVIDER_COLOR_RGB = "FFFFFF"
DIVIDER_ALPHA_HEX = "80"
TOP_LYRIC_TEXT_COLOR_RGB = "FFFFFF"
TOP_LYRIC_TEXT_ALPHA_HEX = "00"
BOTTOM_BOX_BG_COLOR_RGB = "000000"
BOTTOM_BOX_BG_ALPHA_HEX = "00"
TOP_BOX_BG_COLOR_RGB = "000000"
TOP_BOX_BG_ALPHA_HEX = "00"
NEXT_LABEL_COLOR_RGB = "FFFFFF"
NEXT_LABEL_ALPHA_HEX = GLOBAL_NEXT_ALPHA_HEX

DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER = 1.5

# 🔒 Fixed render offset (pipeline may still pass --offset)
LYRICS_OFFSET_SECS = 0.0

MUSIC_NOTE_CHARS = "♪♫♬♩♭♯"
MUSIC_NOTE_KEYWORDS = {"instrumental", "solo", "guitar solo", "piano solo"}

def log(prefix: str, msg: str, color: str = RESET) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{prefix}] {msg}{RESET}")

def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

def read_meta(slug: str) -> tuple[str, str]:
    meta_path = META_DIR / f"{slug}.json"
    artist, title = "", slug
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            artist = data.get("artist") or ""
            title = data.get("title") or title
        except Exception as e:
            log("META", f"Failed to read meta {meta_path}: {e}", YELLOW)
    return artist, title

def read_timings(slug: str):
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if not timing_path.exists():
        log("TIMINGS", f"Missing timing CSV: {timing_path}", RED)
        sys.exit(1)

    rows = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header and "time_secs" in header:
            idx_time = header.index("time_secs")
            idx_text = header.index("text")
            for row in reader:
                try:
                    rows.append((float(row[idx_time]), row[idx_text]))
                except Exception:
                    pass
        else:
            for row in reader:
                try:
                    rows.append((float(row[0]), row[1]))
                except Exception:
                    pass

    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {timing_path}", CYAN)
    return rows

def choose_audio(slug: str) -> Path:
    wav = MIXES_DIR / f"{slug}.wav"
    mp3 = MIXES_DIR / f"{slug}.mp3"
    if wav.exists():
        return wav
    if mp3.exists():
        return mp3
    log("AUDIO", f"No mixed audio found for {slug}", RED)
    sys.exit(1)

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from slug.")
    p.add_argument("--slug", required=True)
    p.add_argument("--font-name", default="Helvetica")
    p.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Accepted for pipeline compatibility (ignored; fixed offset used).",
    )
    return p.parse_args(argv)

def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    if args.offset != 0.0:
        log(
            "OFFSET",
            f"Ignoring CLI --offset {args.offset:.2f}; using fixed {LYRICS_OFFSET_SECS:.2f}",
            YELLOW,
        )
    else:
        log("OFFSET", f"Using fixed LYRICS_OFFSET_SECS={LYRICS_OFFSET_SECS:.2f}", CYAN)

    ui_font_size = DEFAULT_UI_FONT_SIZE
    ass_font_size = int(ui_font_size * ASS_FONT_MULTIPLIER)
    log("FONT", f"UI font size {ui_font_size} → ASS {ass_font_size}", CYAN)

    audio_path = choose_audio(slug)
    artist, title = read_meta(slug)
    _ = read_timings(slug)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_mp4 = OUTPUT_DIR / f"{slug}.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30",
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-shortest",
        str(out_mp4),
    ]

    subprocess.run(cmd, check=True)
    log("MP4", f"Wrote {out_mp4}", GREEN)

if __name__ == "__main__":
    main()

# end of 4_mp4.py
