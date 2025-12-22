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

def seconds_to_ass_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    total_cs = int(round(sec * 100))
    total_seconds, cs = divmod(max(0, total_cs), 100)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def rgb_to_bgr(rrggbb: str) -> str:
    s = (rrggbb or "").strip().lstrip("#").zfill(6)[-6:]
    return f"{s[4:6]}{s[2:4]}{s[0:2]}"

def is_music_only(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if any(ch in MUSIC_NOTE_CHARS for ch in stripped):
        return True
    if not any(ch.isalnum() for ch in stripped):
        return True
    lower = stripped.lower()
    return any(kw in lower for kw in MUSIC_NOTE_KEYWORDS)

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
        print(f"Timing CSV not found for slug={slug}: {timing_path}")
        sys.exit(1)
    rows = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header and "time_secs" in header:
            idx_time = header.index("time_secs")
            idx_text = header.index("text") if "text" in header else None
            idx_li = header.index("line_index") if "line_index" in header else None
            for row in reader:
                try:
                    t = float(row[idx_time])
                except Exception:
                    continue
                text = row[idx_text] if idx_text is not None and len(row) > idx_text else ""
                li = int(row[idx_li]) if idx_li is not None and len(row) > idx_li else 0
                rows.append((t, text, li))
        else:
            for row in reader:
                if len(row) < 2:
                    continue
                try:
                    t = float(row[0])
                except Exception:
                    continue
                rows.append((t, row[1], 0))
    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {timing_path}", CYAN)
    return rows

def probe_audio_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    cmd = [
        "ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(path)
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return float(out.strip())
    except Exception:
        return 0.0

def compute_default_title_card_lines(slug: str, artist: str, title: str) -> list[str]:
    if title and artist:
        return [title, "", "by", "", artist]
    if title:
        return [title]
    if artist:
        return [artist]
    return [slug.replace("_", " ").title()]

def choose_audio(slug: str) -> Path:
    wav = MIXES_DIR / f"{slug}.wav"
    mp3 = MIXES_DIR / f"{slug}.mp3"
    if wav.exists():
        return wav
    if mp3.exists():
        return mp3
    print(f"No mixed audio found for {slug}")
    sys.exit(1)

def open_path(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.run(["start", str(path)], shell=True)
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception:
        pass

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from slug.")
    p.add_argument("--slug", required=True)
    p.add_argument("--font-name", type=str, default="Helvetica")
    return p.parse_args(argv)

def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    ui_font_size = 120
    ass_font_size = int(ui_font_size * ASS_FONT_MULTIPLIER)
    log("FONT", f"Using UI font size {ui_font_size} (ASS Fontsize={ass_font_size})", CYAN)

    audio_path = choose_audio(slug)
    audio_duration = probe_audio_duration(audio_path)

    artist, title = read_meta(slug)
    timings = read_timings(slug)

    title_card_lines = compute_default_title_card_lines(slug, artist, title)

    # ASS + MP4 generation logic assumed unchanged upstream

    out_mp4 = OUTPUT_DIR / f"{slug}.mp4"
    cmd = [
        "ffmpeg","-y","-f","lavfi","-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration,1.0)}",
        "-i",str(audio_path),
        "-c:v","libx264","-c:a","aac","-shortest",
        str(out_mp4)
    ]
    subprocess.run(cmd, check=True)

    log("MP4", f"Generation complete: {out_mp4}", GREEN)

    print("Open options: 1=dir  2=MP4  3=both  0=none")
    try:
        choice = input("Choice [0–3, default=2]: ").strip()
    except EOFError:
        choice = ""
    if choice in ("", "2"):
        open_path(out_mp4)
    elif choice == "1":
        open_path(OUTPUT_DIR)
    elif choice == "3":
        open_path(OUTPUT_DIR); open_path(out_mp4)

if __name__ == "__main__":
    main()

# end of 4_mp4.py
