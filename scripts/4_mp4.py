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

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

# =============================================================================
# LAYOUT CONSTANTS
# =============================================================================
# Fraction of the total video height that is reserved for the *bottom* box
# where the "next lyric" preview text lives. Increase this to make the bottom
# preview box taller, decrease it to give more space to the main (top) lyrics.
BOTTOM_BOX_HEIGHT_FRACTION = 0.20  # 0.20 = 20% of the screen height

# The remaining height is automatically used by the top "current lyric" box.
TOP_BAND_FRACTION = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION

# Vertical margins inside the bottom band for the "next" line text.
NEXT_LYRIC_TOP_MARGIN_PX = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50

# Divider line margins (left/right) as absolute pixels based on VIDEO_WIDTH.
DIVIDER_LEFT_MARGIN_PX = int(VIDEO_WIDTH * 0.035)
DIVIDER_RIGHT_MARGIN_PX = int(VIDEO_WIDTH * 0.035)
DIVIDER_HEIGHT_PX = 0.25

# Where to position the top "current lyric" text vertically. We put it roughly
# in the middle of the top band and let ASS's alignment handle the rest.
TOP_LYRIC_MARGIN_TOP_PX = 200

# Fonts
DEFAULT_UI_FONT_SIZE = 120
MAX_FONT_SIZE = 200
MIN_FONT_SIZE = 20

# Scaling factors for the bottom "next" line and "Next:" label
NEXT_LINE_FONT_SCALE = 0.35
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

# Fade timing (milliseconds) applied to each lyric change for both the main
# lyric and the preview lyric. Divider and "Next:" label do NOT fade.
FADE_IN_MS = 50
FADE_OUT_MS = 50

# =============================================================================
# COLOR AND OPACITY CONSTANTS
# =============================================================================
# All ASS colors are encoded as AABBGGRR (alpha, blue, green, red) under the hood.
# To make configuration less painful, we keep everything as simple hex RRGGBB here
# and build the AABBGGRR/BGGRR codes where needed.

# Global base text + line color for the *bottom* "next lyric" area.
# This does NOT affect the top current-lyric text color; that has its own constant.
GLOBAL_NEXT_COLOR_RGB = "FFFFFF"   # white

# Global alpha for the next-lyric text.
#  - "00" = fully opaque
#  - "FF" = fully transparent
GLOBAL_NEXT_ALPHA_HEX = "4D"       # semi-transparent

# Divider line color and alpha. Defaults to reuse the same color as next text.
DIVIDER_COLOR_RGB = "FFFFFF"       # white
DIVIDER_ALPHA_HEX = "80"           # semi-transparent

# Top lyric (current line) text color and alpha.
TOP_LYRIC_TEXT_COLOR_RGB = "FFFFFF"  # white
TOP_LYRIC_TEXT_ALPHA_HEX = "00"      # fully opaque

# Background color for the bottom "next lyric" rectangle and its alpha.
# Currently just a configuration hook; if you later draw a bottom bar, use both.
BOTTOM_BOX_BG_COLOR_RGB = "000000"    # black
BOTTOM_BOX_BG_ALPHA_HEX = "00"        # fully transparent (no visible bar)

# Background color for the top "current lyric" rectangle and its alpha.
# This drives the style's BackColour.
TOP_BOX_BG_COLOR_RGB = "000000"       # black
TOP_BOX_BG_ALPHA_HEX = "00"           # fully transparent back color for top band

# "Next:" label color and alpha. Separate from GLOBAL_NEXT_* so you can
# tweak the label independently if desired.
NEXT_LABEL_COLOR_RGB = "FFFFFF"        # white
NEXT_LABEL_ALPHA_HEX = GLOBAL_NEXT_ALPHA_HEX

# Global lyric offset (seconds). This is read from an environment variable so
# you can tweak timing without regenerating the CSV.
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "0") or "0")

# Music-only heuristic helpers.
MUSIC_NOTE_CHARS = "♪♫♬♩♭♯"
MUSIC_NOTE_KEYWORDS = [
    "instrumental",
    "solo",
    "intro",
    "outro",
    "bridge",
    "interlude",
    "music only",
]


def rgb_to_bgr(hex_rgb: str) -> str:
    hex_rgb = hex_rgb.strip().lstrip("#")
    if len(hex_rgb) != 6:
        return "FFFFFF"
    r = hex_rgb[0:2]
    g = hex_rgb[2:4]
    b = hex_rgb[4:6]
    return f"{b}{g}{r}"


def seconds_to_ass_time(sec: float) -> str:
    if sec < 0:
        sec = 0
    h = int(sec // 3600)
    rem = sec % 3600
    m = int(rem // 60)
    s = rem % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def read_meta(slug: str) -> tuple[str, str]:
    meta_path = META_DIR / f"{slug}.json"
    artist = ""
    title = slug
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            artist = data.get("artist") or ""
            title = data.get("title") or title
        except Exception as e:
            log("META", f"Failed to read meta {meta_path}: {e}", YELLOW)
    return artist, title


def read_timings(slug: str):
    """
    Return list of (time_secs, text, line_index).
    Expected CSV format:
        line_index,time_secs,text
    """
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if not timing_path.exists():
        print(f"Timing CSV not found for slug={slug}: {timing_path}")
        sys.exit(1)

    rows = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row["line_index"])
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "")
            rows.append((t, text, idx))

    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {timing_path}", CYAN)
    return rows


def probe_audio_duration(audio_path: Path) -> float:
    if not audio_path.exists():
        log("DUR", f"Audio file not found: {audio_path}", RED)
        return 0.0

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
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        val = float(out.decode("utf-8").strip())
    except Exception as e:
        log("DUR", f"ffprobe failed, defaulting duration to 0: {e}", YELLOW)
        val = 0.0
    return max(0.0, val)


def is_music_only(text: str) -> bool:
    """
    Heuristic for lines that are "music only" (notes, emoji, or keywords).
    Used to center the line and hide the bottom rectangle.
    """
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False

    # Explicit music-note characters.
    if any(ch in MUSIC_NOTE_CHARS for ch in stripped):
        return True

    # Only symbols / emoji, no alphanumerics.
    if not any(ch.isalnum() for ch in stripped):
        return True

    # Keyword-based detection.
    lower = stripped.lower()
    for kw in MUSIC_NOTE_KEYWORDS:
        if kw in lower:
            return True

    return False


def build_ass(
    slug: str,
    artist: str,
    title: str,
    timings,
    audio_duration: float,
    font_name: str,
    font_size_script: int,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}.ass"

    if audio_duration <= 0.0 and timings:
        audio_duration = max(t for t, _, _ in timings) + 5.0
    if audio_duration <= 0.0:
        audio_duration = 5.0

    bottom_box_height = int(VIDEO_HEIGHT * BOTTOM_BOX_HEIGHT_FRACTION)
    top_box_height = VIDEO_HEIGHT - bottom_box_height

    y_top_center = int(top_box_height * 0.5)
    y_center_full = VIDEO_HEIGHT // 2
    y_divider_nominal = top_box_height
    y_main_top = y_top_center

    margin_v = TOP_LYRIC_MARGIN_TOP_PX

    top_primary_ass = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    top_back_ass = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"
    secondary_ass = "&H000000FF"
    outline_ass = "&H00000000"
    back_ass = top_back_ass

    header_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        f"PlayResX: {VIDEO_WIDTH}",
        f"PlayResY: {VIDEO_HEIGHT}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{font_name},{font_size_script},"
            f"{top_primary_ass},{secondary_ass},{outline_ass},{back_ass},"
            "0,0,0,0,100,100,0,0,1,4,0,5,50,50,"
            f"{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ass_escape(text: str) -> str:
        text = text.replace("{", "(").replace("}", ")")
        text = text.replace("\\N", "\\N")
        text = text.replace("\n", r"\N")
        return text

    events = []

    # Normalize timings and filter.
    unified = []
    for t, text, line_index in timings:
        if t < 0 or (audio_duration and t > audio_duration):
            continue
        text = (text or "").strip()
        if not text:
            continue
        unified.append((t, text, line_index))

    unified.sort(key=lambda x: x[0])

    offset = LYRICS_OFFSET_SECS

    # Detect whether timings CSV appears partial. If the gap between
    # the last timed line (plus offset) and the end of the audio is large,
    # avoid holding the final line on screen all the way to song end.
    is_partial = False
    if unified and audio_duration:
        last_start = unified[-1][0] + offset
        if audio_duration - last_start > 15.0:
            is_partial = True

    # If no timings, just show centered title card for whole song.
    if not unified:
        title_lines = []
        if title:
            title_lines.append(title)
        if artist:
            title_lines.append(f"by {artist}")
        if not title_lines:
            title_lines = ["No lyrics"]

        intro_block = "\\N".join(title_lines)
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(0.0),
                end=seconds_to_ass_time(audio_duration),
                text=f"{{\\an5\\bord2\\shad0}}{intro_block}",
            )
        )
        ass_path.write_text("\n".join(header_lines + events) + "\n", encoding="utf-8")
        log("ASS", f"Wrote ASS subtitles (title only) to {ass_path}", GREEN)
        return ass_path

    n = len(unified)
    playresx = VIDEO_WIDTH

    bottom_box_top = top_box_height
    bottom_box_bottom = VIDEO_HEIGHT

    next_line_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))

    next_color_bgr = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color_bgr = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color_bgr = rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    divider_height = max(0.5, float(DIVIDER_HEIGHT_PX))
    left_margin = float(DIVIDER_LEFT_MARGIN_PX)
    right_margin = float(DIVIDER_RIGHT_MARGIN_PX)
    x_left = left_margin
    x_right = playresx - right_margin
    if x_right <= x_left:
        x_left = 0.0
        x_right = float(playresx)

    label_x = NEXT_LABEL_LEFT_MARGIN_PX
    label_y = y_divider_nominal + NEXT_LABEL_TOP_MARGIN_PX

    # Per-line events.
    for i, (t, raw_text, _line_index) in enumerate(unified):
        start = max(0.0, t + offset)
        if i < n - 1:
            end = max(start, unified[i + 1][0] + offset)
        else:
            if is_partial:
                end = start + 3.0
            else:
                end = audio_duration or (start + 5.0)

        if end > audio_duration:
            end = audio_duration
        if end <= start:
            continue

        text_stripped = raw_text.strip()
        music_only = is_music_only(text_stripped)

        # Main lyric line (with fade).
        main_text = ass_escape(text_stripped)

        # Top band: center horizontally, near top, fade in/out.
        fade_in = FADE_IN_MS
        fade_out = FADE_OUT_MS

        if music_only:
            main_tag = f"{{\\an5\\bord2\\shad1\\fad({fade_in},{fade_out})}}"
        else:
            main_tag = f"{{\\an8\\bord2\\shad1\\fad({fade_in},{fade_out})}}"

        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=main_tag + main_text,
            )
        )

        # Decide if we should draw the bottom "Next" UI.
        is_last = (i == n - 1)
        show_bottom = (not music_only) and (not is_last)

        if not show_bottom:
            continue

        # Bottom band "Next" preview.
        next_text_raw = unified[i + 1][1].strip()
        next_body = ass_escape(next_text_raw)

        next_fade_in = FADE_IN_MS
        next_fade_out = FADE_OUT_MS

        preview_tag = (
            f"{{\\an8\\fs{next_line_font}\\bord1\\shad0"
            f"\\pos({playresx // 2},{bottom_box_top + NEXT_LYRIC_TOP_MARGIN_PX})"
            f"\\1c&H{next_color_bgr}&"
            f"\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&"
            f"\\fad({next_fade_in},{next_fade_out})}}"
        )

        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=preview_tag + next_body,
            )
        )

        # Divider line as a thin filled rectangle.
        divider_tag = (
            f"{{\\an8\\bord0\\shad0"
            f"\\1c&H{divider_color_bgr}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\p1}}"
        )
        divider_shape = (
            f"m {x_left} {y_divider_nominal} "
            f"l {x_right} {y_divider_nominal} "
            f"l {x_right} {y_divider_nominal + divider_height} "
            f"l {x_left} {y_divider_nominal + divider_height} "
            f"l {x_left} {y_divider_nominal}"
            "{\\p0}"
        )
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=divider_tag + divider_shape,
            )
        )

        # "Next:" label (no fade).
        label_tag = (
            f"{{\\an7\\pos({label_x},{label_y})"
            f"\\fs{next_label_font}"
            f"\\1c&H{next_label_color_bgr}&"
            f"\\1a&H{NEXT_LABEL_ALPHA_HEX}&}}"
        )
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=label_tag + "Next:",
            )
        )

    ass_path.write_text("\n".join(header_lines + events) + "\n", encoding="utf-8")
    log("ASS", f"Wrote ASS subtitles to {ass_path}", GREEN)
    return ass_path


def choose_audio(slug: str, profile: str) -> Path:
    """
    Choose audio file:
      - Prefer mixes/{slug}_{profile}.wav if it exists.
      - Else fall back to mp3s/{slug}.mp3.
    """
    wav_path = MIXES_DIR / f"{slug}_{profile}.wav"
    if wav_path.exists():
        return wav_path
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if mp3_path.exists():
        return mp3_path
    raise SystemExit(f"No audio found for slug={slug}, profile={profile}")


def open_path(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.run(["start", str(path)], shell=True)
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception as e:
        log("OPEN", f"Failed to open {path}: {e}", YELLOW)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from slug/profile.")
    p.add_argument("--slug", required=True, help="Song slug, e.g. californication")
    p.add_argument(
        "--profile",
        required=True,
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
        help="Mix profile name (matches WAV/MP3 name in mixes/).",
    )
    p.add_argument(
        "--font-size",
        type=int,
        help="Subtitle font size (20–200). Default 120.",
    )
    p.add_argument(
        "--font-name",
        type=str,
        default="Helvetica",
        help="Subtitle font name. Default Helvetica.",
    )
    p.add_argument(
        "--output-mp4",
        type=str,
        help="Optional explicit output MP4 path. Defaults to output/{slug}_{profile}.mp4.",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)
    profile = args.profile

    default_font_size = DEFAULT_UI_FONT_SIZE
    font_size_value = args.font_size

    if font_size_value is None:
        if sys.stdin.isatty():
            try:
                resp = input(
                    f"Subtitle font size [20–200, default {default_font_size}]: "
                ).strip()
            except EOFError:
                resp = ""
            if not resp:
                font_size_script = default_font_size
            else:
                try:
                    v = int(resp)
                    font_size_script = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, v))
                except ValueError:
                    font_size_script = default_font_size
        else:
            font_size_script = default_font_size
    else:
        font_size_script = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, font_size_value))

    log(
        "ARGS",
        f"slug={slug}, profile={profile}, font={args.font_name}, size={font_size_script}",
        CYAN,
    )

    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0.0:
        log("DUR", f"Audio duration unknown or zero for {audio_path}", YELLOW)

    artist, title = read_meta(slug)
    timings = read_timings(slug)
    log("META", f'Artist="{artist}", Title="{title}", entries={len(timings)}', CYAN)

    ass_path = build_ass(
        slug, artist, title, timings, audio_duration, args.font_name, font_size_script
    )

    out_mp4 = Path(args.output_mp4) if getattr(args, "output_mp4", None) else OUTPUT_DIR / f"{slug}_{profile}.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration, 1.0)}",
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles={ass_path}",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
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
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    log("MP4", f"Wrote MP4 to {out_mp4} in {t1 - t0:6.2f} s", GREEN)

    try:
        choice = input(
            "Open output folder [1], open MP4 [2], open both [3], or ENTER to skip: "
        ).strip()
    except EOFError:
        choice = ""

    if choice == "1":
        open_path(OUTPUT_DIR)
    elif choice == "2":
        open_path(out_mp4)
    elif choice == "3":
        open_path(OUTPUT_DIR)
        open_path(out_mp4)
    else:
        log("OPEN", "No open action selected.", YELLOW)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
