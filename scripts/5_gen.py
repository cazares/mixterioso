#!/usr/bin/env python3
# scripts/5_gen.py
#
# STEP 5: Generate MP4 (formerly 4_mp4.py), minimally altered.
# -----------------------------------------------------------
# - Identical rendering behavior to your LKWV
# - Only safe adjustments:
#     * accept --base-filename
#     * support offset passthrough from master
#     * output final mp4 as: output/<slug>.mp4
#     * JSON output at end for 0_master
#     * optional post-render offset tweak stub
#
# EVERYTHING ELSE IS UNCHANGED. ALL YOUR NOTE LOGIC REMAINS EXACT.

from __future__ import annotations
import argparse
import csv
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER = 1.5

TOP_BAND_FRACTION = 0.52
BOTTOM_BAND_FRACTION = 1.0 - TOP_BAND_FRACTION

LYRICS_OFFSET_SECS = 0.0

NOTE_FONT = "Helvetica"

NOTE_FONT_SIZE_BASE = 120
NOTE_Y_FRACTION = 0.28
NOTE_MAX_SPREAD_X = 0.15

NOTE_GLYPHS = [
    "♫",
    "♪",
    "♬",
    "♩",
    "𝄞",
    "𝄢",
    "🤍",
    "♡",
]

NOTE_COLOR_RGB = "FFFFFF"
NOTE_ALPHA_HEX = "C0"

NOTE_CHANGE_INTERVAL_SECS = 4.0
NOTE_RANDOM_X_MIN = -0.08
NOTE_RANDOM_X_MAX = 0.08

VERTICAL_OFFSET_FRACTION = 0.0

NEXT_LINE_FONT_SCALE = 0.55
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = 40

FADE_IN_MS = 20
FADE_OUT_MS = 40

GLOBAL_NEXT_COLOR_RGB = "FFFFFF"
GLOBAL_NEXT_ALPHA_HEX = "4D"

DIVIDER_COLOR_RGB = "FFFFFF"
DIVIDER_ALPHA_HEX = "80"
DIVIDER_HEIGHT_PX = 4
DIVIDER_LEFT_MARGIN_PX = 80
DIVIDER_RIGHT_MARGIN_PX = 80

MUSIC_NOTE_CHARS = "".join(NOTE_GLYPHS)

# -------------------------------------------------------------------------
# Logging helpers
# -------------------------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)


# -------------------------------------------------------------------------
# Slug helpers
# -------------------------------------------------------------------------
def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# -------------------------------------------------------------------------
# Read timings
# -------------------------------------------------------------------------
def read_timings(slug):
    """
    Read canonical timings for slug.

    Primary canonical format is:
        line_index,start,end,text

    But this helper also tolerates legacy:
        line_index,time_secs,text
    by synthesizing end = start + 2.5s.
    """
    p = TIMINGS_DIR / f"{slug}.csv"
    if not p.exists():
        raise SystemExit(f"Timings CSV not found: {p}")

    rows = []
    with p.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit(f"Empty timings CSV: {p}")
        headers = [h.strip().lower() for h in reader.fieldnames]
        has_start_end = "start" in headers and "end" in headers
        is_legacy = "time_secs" in headers and "text" in headers

        if has_start_end:
            for row in reader:
                try:
                    li = int(row["line_index"])
                    st = float(row["start"])
                    en = float(row["end"])
                    text = row.get("text", "")
                except Exception:
                    continue
                rows.append((li, st, en, text))
        elif is_legacy:
            for row in reader:
                try:
                    li = int(row["line_index"])
                    st = float(row["time_secs"])
                    text = row.get("text", "")
                except Exception:
                    continue
                en = st + 2.5
                rows.append((li, st, en, text))
        else:
            raise SystemExit(
                f"Unexpected CSV header for {p}: {reader.fieldnames} "
                f"(expected start/end or time_secs)"
            )

    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


# -------------------------------------------------------------------------
# Probe audio duration
# -------------------------------------------------------------------------
def probe_audio_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
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
        return 0.0


# -------------------------------------------------------------------------
# Choose audio (mix or mp3)
# -------------------------------------------------------------------------
def choose_audio(slug, profile):
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mix_wav.exists():
        return mix_wav
    return mp3


# -------------------------------------------------------------------------
# Read meta (artist/title)
# -------------------------------------------------------------------------
def read_meta(slug):
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return "", slug
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        artist = data.get("artist", "")
        title = data.get("title", slug)
        return artist, title
    except Exception:
        return "", slug


# -------------------------------------------------------------------------
# ASS helpers
# -------------------------------------------------------------------------
def sec_to_ass(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    if s == 60:
        s = 0
        m += 1
    if m == 60:
        m = 0
        h += 1
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def offset_tag(offset: float) -> str:
    if abs(offset) < 0.0005:
        return ""
    sign = "p" if offset > 0 else "m"
    val = abs(offset)
    return f"_offset_{sign}{val:.3f}s".replace(".", "p")


def ass_header(playresx, playresy, font_name, font_size_script):
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {playresx}
PlayResY: {playresy}
Collisions: Normal
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font_name},{font_size_script},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,2,80,80,40,1
Style: Next,{font_name},{int(font_size_script*NEXT_LINE_FONT_SCALE)},&H00{GLOBAL_NEXT_COLOR_RGB},&H00{GLOBAL_NEXT_COLOR_RGB},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,80,80,40,1
Style: Label,{font_name},{int(font_size_script*NEXT_LABEL_FONT_SCALE)},&H00{GLOBAL_NEXT_COLOR_RGB},&H00{GLOBAL_NEXT_COLOR_RGB},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,80,80,40,1
Style: Note,{NOTE_FONT},{NOTE_FONT_SIZE_BASE},&H00{NOTE_COLOR_RGB},&H00{NOTE_COLOR_RGB},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,80,80,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# -------------------------------------------------------------------------
# Notes layout helpers
# -------------------------------------------------------------------------
def random_note_positions(num_notes: int, base_x: float, base_y: float):
    xs = []
    for i in range(num_notes):
        jitter = random.uniform(NOTE_RANDOM_X_MIN, NOTE_RANDOM_X_MAX)
        xs.append(base_x + jitter + i * 0.02)
    return xs


# -------------------------------------------------------------------------
# Misc helpers
# -------------------------------------------------------------------------
def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def rgb_to_ass_hex(rgb: str) -> str:
    rgb = rgb.strip()
    if len(rgb) != 6:
        return "FFFFFF"
    try:
        int(rgb, 16)
        return rgb.upper()
    except ValueError:
        return "FFFFFF"


def rgba_tag(rgb: str, alpha_hex: str) -> str:
    rgb = rgb_to_ass_hex(rgb)
    if len(alpha_hex) != 2:
        alpha_hex = "00"
    bb = rgb[4:6]
    gg = rgb[2:4]
    rr = rgb[0:2]
    return f"{bb}{gg}{rr}"


def is_music_only(text):
    if not text:
        return False
    t = text.strip()
    if not t:
        return False
    if any(ch.isalnum() for ch in t):
        return False
    if any(ch in MUSIC_NOTE_CHARS for ch in t):
        return True
    lower = t.lower()
    for kw in ["instrumental", "solo", "guitar solo", "piano solo"]:
        if kw in lower:
            return True
    return True


def random_note():
    # Placeholder left from previous experimentation, not used in current pipeline.
    return random.choice(NOTE_GLYPHS)


# -------------------------------------------------------------------------
# Build ASS with lines + “Next” + notes
# -------------------------------------------------------------------------
def build_ass(
    slug: str,
    profile: str,
    artist: str,
    title: str,
    timings,
    audio_duration: float,
    font_name: str,
    font_size_script: int,
    offset_applied: float,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset_applied)}.ass"

    if audio_duration <= 0 and timings:
        audio_duration = max(end for _, end, _, _ in timings) + 5
    if audio_duration <= 0:
        audio_duration = 5

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_div = top_band_height
    bottom_band_height = playresy - y_div

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px

    x_center = playresx // 2
    y_center_full = playresy // 2
    y_next = (
        y_div
        + NEXT_LABEL_TOP_MARGIN_PX
        + (bottom_band_height - NEXT_LABEL_TOP_MARGIN_PX - NEXT_LABEL_TOP_MARGIN_PX) // 2
    )

    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))

    with ass_path.open("w", encoding="utf-8") as f:
        f.write(ass_header(playresx, playresy, font_name, font_size_script))

        divider_y = y_div
        f.write(
            f"Dialogue: 0,{sec_to_ass(0)},{sec_to_ass(audio_duration)},Label,,0,0,0,,"
            f"{{\\bord0\\shad0\\pos({playresx//2},{divider_y})"
            f"\\c&H{DIVIDER_COLOR_RGB}&\\alpha&H{DIVIDER_ALPHA_HEX}&"
            f"\\p1}}m 0 0 l {playresx} 0 l {playresx} {DIVIDER_HEIGHT_PX} l 0 {DIVIDER_HEIGHT_PX}{{\\p0}}\\N"
            "\\N"
            "\n"
        )

        title_text = title or slug
        artist_text = artist or ""
        main_title = title_text if not artist_text else f"{artist_text} — {title_text}"

        f.write(
            f"Dialogue: 0,{sec_to_ass(0)},{sec_to_ass(5)},Main,,0,0,0,,"
            f"{{\\an8\\pos({x_center},{int(y_main_top*0.4)})}}{main_title}\n"
        )

        for (idx, start, end, text) in timings:
            if idx < 0 or not text.strip():
                continue
            start_adj = max(0.0, start + offset_applied)
            end_adj = max(start_adj + 0.01, end + offset_applied)
            f.write(
                f"Dialogue: 0,{sec_to_ass(start_adj)},{sec_to_ass(end_adj)},Main,,0,0,0,,"
                f"{{\\an5\\pos({x_center},{y_main_top})}}{text}\n"
            )

        next_label = "Next:"
        for i, (idx, start, end, text) in enumerate(timings):
            if idx < 0 or not text.strip():
                continue
            start_adj = max(0.0, start + offset_applied)
            end_adj = max(start_adj + 0.01, end + offset_applied)
            f.write(
                f"Dialogue: 0,{sec_to_ass(start_adj)},{sec_to_ass(end_adj)},Label,,0,0,0,,"
                f"{{\\an1\\pos({NEXT_LABEL_LEFT_MARGIN_PX},{y_next})}}{next_label}\n"
            )
            f.write(
                f"Dialogue: 0,{sec_to_ass(start_adj)},{sec_to_ass(end_adj)},Next,,0,0,0,,"
                f"{{\\an4\\pos({x_center},{y_next})}}{text}\n"
            )

        note_interval = NOTE_CHANGE_INTERVAL_SECS
        num_notes = min(len(NOTE_GLYPHS), 5)
        note_base_x = 0.75
        note_base_y = NOTE_Y_FRACTION * top_band_height

        t = 0.0
        while t < audio_duration:
            t_start = t
            t_end = min(audio_duration, t + note_interval)

            glyphs = random.sample(NOTE_GLYPHS, k=num_notes)
            xs = random_note_positions(
                num_notes, note_base_x, NOTE_Y_FRACTION * playresy
            )

            for g, x in zip(glyphs, xs):
                px = int(x * playresx)
                py = int(note_base_y)
                f.write(
                    f"Dialogue: 0,{sec_to_ass(t_start)},{sec_to_ass(t_end)},Note,,0,0,0,,"
                    f"{{\\an5\\pos({px},{py})}}{g}\n"
                )

            t += note_interval

    return ass_path


# -------------------------------------------------------------------------
def main(argv=None):
    global LYRICS_OFFSET_SECS

    p = argparse.ArgumentParser()
    p.add_argument("--base-filename", required=True)
    p.add_argument("--profile", default="karaoke")
    p.add_argument("--font-size", type=int)
    p.add_argument("--font-name", default="Helvetica")
    p.add_argument("--offset", type=float)
    p.add_argument("--force", action="store_true")
    # NEW: accept artist/title overrides from master
    p.add_argument("--artist", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("passthrough", nargs="*")
    args = p.parse_args(argv)

    slug = slugify(args.base_filename)

    if args.offset is not None:
        LYRICS_OFFSET_SECS = float(args.offset)

    log("Gen", "Offset = {:.3f}s".format(LYRICS_OFFSET_SECS), CYAN)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_mp4 = OUTPUT_DIR / f"{slug}.mp4"

    font_size = args.font_size or DEFAULT_UI_FONT_SIZE
    ass_font_size = int(font_size * ASS_FONT_MULTIPLIER)

    audio_path = choose_audio(slug, args.profile)
    audio_duration = probe_audio_duration(audio_path)

    artist, title = read_meta(slug)
    # NEW: allow CLI to override meta
    if getattr(args, "artist", None):
        artist = args.artist
    if getattr(args, "title", None):
        title = args.title
    timings = read_timings(slug)

    ass_path = build_ass(
        slug,
        args.profile,
        artist,
        title,
        timings,
        audio_duration,
        args.font_name,
        ass_font_size,
        LYRICS_OFFSET_SECS,
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration,1)}",
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles={ass_path}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_mp4),
    ]

    log("FFMPEG", " ".join(cmd), BLUE)

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in proc.stdout:
        print(f"{CYAN}[ffmpeg]{RESET} {line.rstrip()}")
    proc.wait()

    if proc.returncode != 0:
        print(json.dumps({"ok": False, "error": "ffmpeg-failed"}))
        return

    log("Gen", f"MP4 ready: {out_mp4}", GREEN)

    # Optional offset tweak hook (stub)
    # TODO: ask user "Adjust offset further? (y/n)" → re-render
    # For now, leave stub.

    print(
        json.dumps(
            {
                "ok": True,
                "slug": slug,
                "mp4": str(out_mp4),
                "mp4_path": str(out_mp4),
                "ass": str(ass_path),
                "offset": LYRICS_OFFSET_SECS,
            }
        )
    )


if __name__ == "__main__":
    main()

# end of 5_gen.py
