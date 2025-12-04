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

# =============================================================================
# LAYOUT CONSTANTS
# =============================================================================
# Fraction of the total video height that is reserved for the *bottom* box
# where the "next lyric" preview text lives. Increase this to make the bottom
# preview box taller, decrease it to give more space to the main (top) lyrics.
BOTTOM_BOX_HEIGHT_FRACTION = 0.20  # 0.20 = 20% of the screen height

# The remaining height is automatically used by the top "current lyric" box.
TOP_BAND_FRACTION = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION

# Vertical padding (in pixels) inside the bottom box around the NEXT lyric text.
# These control how far the preview text sits away from the top and bottom
# edges of the bottom box. The preview baseline is centered between these
# margins.
NEXT_LYRIC_TOP_MARGIN_PX = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50

# Vertical offset (in pixels) by which the thin divider line between the
# current-lyric region and the next-lyric region is moved upward from the
# top edge of the bottom box. Positive values move the line up, negative
# values move it down.
DIVIDER_LINE_OFFSET_UP_PX = 0

# Total height of the divider line shape in pixels. Can be fractional for
# anti-aliased "hairline" looks. 1.0 ~= 1px at PlayResY=VIDEO_HEIGHT.
DIVIDER_HEIGHT_PX = 0.25

# Horizontal margins for the divider line, in pixels. These are measured
# from the left/right edges of the video frame. Set to 0 for edge-to-edge.
DIVIDER_LEFT_MARGIN_PX = VIDEO_WIDTH * 0.035
DIVIDER_RIGHT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX

# Within the top band, you can nudge the main line up or down by changing
# this fraction of the top-band height. Positive values move text DOWN.
VERTICAL_OFFSET_FRACTION = 0.0

# Extra nudge for the title line relative to the main line (fraction of top band).
TITLE_EXTRA_OFFSET_FRACTION = -0.20

# How big the next-lyric text is relative to the main lyric text.
#  0.35 = 35% of the main font size.
NEXT_LINE_FONT_SCALE = 0.475

# How big the "Next:" label text is relative to the main lyric text.
# By default this is smaller than the actual preview line.
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.55

# Margins for the "Next:" label within the bottom box. The label is
# placed at the top-left corner of the bottom rectangle with these offsets.
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

# Fade timing (milliseconds) applied to each lyric change.
# Only used for the main lyric line and the preview ("next line") text.
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

# Global alpha for the next-lyric text and the divider line.
#  - "00" = fully opaque
#  - "FF" = fully transparent
GLOBAL_NEXT_ALPHA_HEX = "4D"  # semi-transparent

# Divider line color and alpha. Defaults to reuse the same color as the
# next-lyric text but with an independently tweakable opacity.
DIVIDER_COLOR_RGB = "FFFFFF"
DIVIDER_ALPHA_HEX = "80"           # semi-transparent divider

# Top (current lyric) font color and alpha.
TOP_LYRIC_TEXT_COLOR_RGB = "FFFFFF"    # white
TOP_LYRIC_TEXT_ALPHA_HEX = "00"       # fully opaque

# Background color for the bottom "next lyric" rectangle and its alpha.
# Currently just a configuration hook; if you later draw a bottom bar, use both.
BOTTOM_BOX_BG_COLOR_RGB = "000000"    # black
BOTTOM_BOX_BG_ALPHA_HEX = "00"        # fully transparent (no visible bar)

# Background color for the top "current lyric" rectangle and its alpha.
# This drives the style's BackColour.
TOP_BOX_BG_COLOR_RGB = "000000"       # black
TOP_BOX_BG_ALPHA_HEX = "00"           # 50% opaque back color for top band

# "Next:" label color and alpha. Separate from GLOBAL_NEXT_* so you can
# tweak the label independently if desired.
NEXT_LABEL_COLOR_RGB = "FFFFFF"        # white
NEXT_LABEL_ALPHA_HEX = GLOBAL_NEXT_ALPHA_HEX  # semi-transparent label

# Base UI font size in "points" (converted to ASS by a multiplier).
DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER = 1.5  # multiple of UI font size to get ASS fontsize

# Global lyrics timing offset in seconds. Positive = delay, negative = earlier.
# This is applied uniformly to all lyric timestamps at render time so you can
# nudge the whole subtitle track without re-running timing.
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "-1.5") or "-1.5")
# If you prefer hardcoded only, comment the line above and do e.g.:
# LYRICS_OFFSET_SECS = -0.35  # shift lyrics 350 ms earlier

# Simple heuristics for "music only" lines.
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
    # ASS time format: H:MM:SS.cs (centiseconds)
    if sec < 0:
        sec = 0.0
    total_cs = int(round(sec * 100))
    if total_cs < 0:
        total_cs = 0
    total_seconds, cs = divmod(total_cs, 100)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rgb_to_bgr(rrggbb: str) -> str:
    """
    Convert an RRGGBB hex string into BGR order as required by ASS (&HAABBGGRR).
    Example:
        "FFFFFF" -> "FFFFFF"
        "FF0000" -> "0000FF"
    """
    s = (rrggbb or "").strip().lstrip("#")
    s = s.zfill(6)[-6:]
    rr = s[0:2]
    gg = s[2:4]
    bb = s[4:6]
    return f"{bb}{gg}{rr}"


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
    Preferred CSV format:
        line_index,time_secs,text
    Fallback 2-column format:
        time_secs,text   (line_index is treated as 0).
    """
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if not timing_path.exists():
        print(f"Timing CSV not found for slug={slug}: {timing_path}")
        sys.exit(1)

    rows = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        if header and "time_secs" in header:
            try:
                idx_time = header.index("time_secs")
            except ValueError:
                idx_time = 1
            try:
                idx_li = header.index("line_index")
            except ValueError:
                idx_li = None
            idx_text = header.index("text") if "text" in header else None

            for row in reader:
                if not row or len(row) <= idx_time:
                    continue
                t_str = row[idx_time].strip()
                if not t_str:
                    continue
                try:
                    t = float(t_str)
                except ValueError:
                    continue

                if idx_li is not None and len(row) > idx_li:
                    try:
                        line_index = int(row[idx_li])
                    except ValueError:
                        line_index = 0
                else:
                    line_index = 0

                text = ""
                if idx_text is not None and len(row) > idx_text:
                    text = row[idx_text]

                rows.append((t, text, line_index))
        else:
            for row in reader:
                if len(row) < 2:
                    continue
                t_str = row[0].strip()
                if not t_str:
                    continue
                try:
                    t = float(t_str)
                except ValueError:
                    continue
                text = row[1]
                rows.append((t, text, 0))

    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {timing_path}", CYAN)
    return rows


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
    log("FFPROBE", f"Probing duration of {path}", BLUE)
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return float(out.strip())
    except Exception as e:
        log("FFPROBE", f"Failed to probe duration: {e}", YELLOW)
        return 0.0


# =============================================================================
# TITLE CARD OVERRIDE (INTERACTIVE)
# =============================================================================
def compute_default_title_card_lines(slug: str, artist: str, title: str) -> list[str]:
    """
    Default title card format:
    
        Title
        (blank)
        by
        (blank)
        Artist
    """
    pretty_slug = slug.replace("_", " ").title()

    if title and artist:
        return [
            title,
            "",
            "by",
            "",
            artist,
        ]

    if title:
        return [title]

    if artist:
        return [artist]

    return [pretty_slug]

def prompt_title_card_lines(slug: str, artist: str, title: str) -> list[str]:
    """
    Interactively allow a temporary per-render override of the title card.
    Never modifies meta.json.
    """
    default_lines = compute_default_title_card_lines(slug, artist, title)

    if not sys.stdin.isatty():
        log("TITLE", "Non-interactive mode; using default title card.", CYAN)
        return default_lines

    print()
    print(f"{CYAN}Title Card Preview (before lyrics):{RESET}")
    print("  Default card would say:\n")
    for line in default_lines:
        print(f"    {line}")
    print()
    print("Options:")
    print("  1) Use default")
    print("  2) Edit title card text manually")
    print()

    while True:
        try:
            choice = input("Choose [1/2, ENTER=1]: ").strip()
        except EOFError:
            choice = ""

        if choice in ("", "1"):
            return default_lines
        if choice == "2":
            break
        print("Please choose 1 or 2.")

    def edit_line(label: str, current: str) -> str:
        try:
            raw = input(f"{label} [{current}]: ").strip()
        except EOFError:
            raw = ""
        return current if raw == "" else raw

    while True:
        print()
        print("Edit title card lines (ENTER keeps default).")
        print("Leave empty lines if desired (blank lines preserved).")
        print()

        # We preserve 5-line structure like the default
        bases = default_lines + ["", "", "", "", ""]
        line1 = edit_line("Line 1", bases[0])
        line2 = edit_line("Line 2", bases[1])
        line3 = edit_line("Line 3", bases[2])
        line4 = edit_line("Line 4", bases[3])
        line5 = edit_line("Line 5", bases[4])

        lines = [line1, line2, line3, line4, line5]

        # Do not allow fully-empty card
        if not any(l.strip() for l in lines):
            print("Title card cannot be completely empty.")
            continue

        print()
        print("Final title card:")
        for l in lines:
            print(f"    {l}")
        print()

        try:
            ok = input("Use this title card? [Y/n]: ").strip().lower()
        except EOFError:
            ok = "y"

        if ok in ("", "y", "yes"):
            log("TITLE", "Using custom title card override.", GREEN)
            return lines

def build_ass(
    slug: str,
    artist: str,
    title: str,
    timings,
    audio_duration: float,
    font_name: str,
    font_size_script: int,
    title_card_lines: list[str] | None = None,
) -> Path:

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}.ass"

    if audio_duration <= 0.0:
        if timings:
            audio_duration = max(t for t, _, _ in timings) + 5
        else:
            audio_duration = 5.0

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Geometry
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_divider_nominal = top_band_height
    bottom_band_height = playresy - y_divider_nominal
    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px
    y_center_full = playresy // 2

    # Divider + next baseline
    inner_bottom = max(
        1,
        bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX,
    )
    y_next = y_divider_nominal + NEXT_LYRIC_TOP_MARGIN_PX + inner_bottom // 2
    line_y = max(0, y_divider_nominal - DIVIDER_LINE_OFFSET_UP_PX)

    # Fonts
    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))

    # Colors for ASS
    top_primary_ass = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    secondary_ass = "&H000000FF"
    outline_ass = "&H00000000"
    back_ass = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"

    header_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: {}".format(playresx),
        "PlayResY: {}".format(playresy),
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
            f"0,0,0,0,100,100,0,0,1,4,0,5,50,50,0,0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ass_escape(text: str):
        return text.replace("{", "(").replace("}", ")").replace("\n", r"\N")

    events = []

    # Filter timings
    unified = []
    for t, raw, idx in timings:
        if 0 <= t <= audio_duration:
            raw = (raw or "").strip()
            if raw:
                unified.append((t, raw, idx))

    unified.sort(key=lambda x: x[0])
    offset = LYRICS_OFFSET_SECS

    # Title card lines
    if title_card_lines:
        title_lines = title_card_lines
    else:
        title_lines = compute_default_title_card_lines(slug, artist, title)

    intro_text = ass_escape("\\N".join(title_lines))

    # CASE A — NO LYRICS AT ALL
    if len(unified) == 0:
        events.append(
            "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
                seconds_to_ass_time(0.0),
                seconds_to_ass_time(audio_duration),
                f"{{\\an5\\pos({playresx//2},{playresy//2})}}{intro_text}",
            )
        )
        ass_path.write_text("\n".join(header_lines + events), encoding="utf-8")
        return ass_path

    # CASE B — NORMAL SONG WITH INTRO (first lyric ≥ 0)
    first_lyric_start = max(0.0, unified[0][0] + offset)
    title_end = min(5.0, first_lyric_start)

    events.append(
        "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
            seconds_to_ass_time(0.0),
            seconds_to_ass_time(title_end),
            f"{{\\an5\\pos({playresx//2},{playresy//2})}}{intro_text}",
        )
    )

    # Fade effects
    fade_tag_main = ""
    if FADE_IN_MS > 0 or FADE_OUT_MS > 0:
        fade_tag_main = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})"

    # Render lyrics + next preview
    left = float(DIVIDER_LEFT_MARGIN_PX)
    right = float(playresx - DIVIDER_RIGHT_MARGIN_PX)
    divider_height = max(0.5, float(DIVIDER_HEIGHT_PX))
    next_color_bgr = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color_bgr = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color_bgr = rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    n = len(unified)
    for i, (t, raw, _) in enumerate(unified):
        start = max(0.0, t + offset)
        end = unified[i + 1][0] + offset if i < n - 1 else audio_duration

        if end <= start:
            continue

        text_stripped = raw.strip()
        music_only = is_music_only(text_stripped)

        # MAIN LYRIC
        y_line = y_center_full if music_only else y_main_top
        events.append(
            "Dialogue: 1,{},{},Default,,0,0,0,,{}".format(
                seconds_to_ass_time(start),
                seconds_to_ass_time(end),
                f"{{\\an5\\pos({playresx//2},{y_line}){fade_tag_main}}}{ass_escape(text_stripped)}",
            )
        )

        # Skip bottom UI cases
        if i == n - 1:
            continue
        next_text = unified[i + 1][1]
        if not next_text or music_only or is_music_only(next_text):
            continue

        # Divider
        divider = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color_bgr}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0\\p1}}"
            f"m {left} 0 l {right} 0 l {right} {divider_height} l {left} {divider_height}{{\\p0}}"
        )
        events.append(
            "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
                seconds_to_ass_time(start),
                seconds_to_ass_time(end),
                divider,
            )
        )

        # Next: label
        events.append(
            "Dialogue: 0,{},{},Default,,0,0,0,,{}".format(
                seconds_to_ass_time(start),
                seconds_to_ass_time(end),
                (
                    f"{{\\an7\\pos({NEXT_LABEL_LEFT_MARGIN_PX},{line_y + NEXT_LABEL_TOP_MARGIN_PX})"
                    f"\\fs{next_label_font}"
                    f"\\1c&H{next_label_color_bgr}&"
                    f"\\1a&H{NEXT_LABEL_ALPHA_HEX}&}}Next:"
                ),
            )
        )

        # Preview line
        preview = (
            f"{{\\an5\\pos({playresx//2},{y_next})"
            f"\\fs{preview_font}"
            f"\\1c&H{next_color_bgr}&"
            f"\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&"
            f"{fade_tag_main}}}{ass_escape(next_text)}"
        )
        events.append(
            "Dialogue: 2,{},{},Default,,0,0,0,,{}".format(
                seconds_to_ass_time(start),
                seconds_to_ass_time(end),
                preview,
            )
        )

    ass_path.write_text("\n".join(header_lines + events), encoding="utf-8")
    return ass_path

def choose_audio(slug: str) -> Path:
    """
    Always use mixes/<slug>.wav if it exists.
    If WAV is missing but mixes/<slug>.mp3 exists, use that.
    Never fall back to the original mp3 again.
    """
    mix_wav = MIXES_DIR / f"{slug}.wav"
    mix_mp3 = MIXES_DIR / f"{slug}.mp3"

    if mix_wav.exists():
        print(f"[AUDIO] Using mixed WAV: {mix_wav}")
        return mix_wav

    if mix_mp3.exists():
        print(f"[AUDIO] Using mixed MP3: {mix_mp3}")
        return mix_mp3

    print(
        f"\n[AUDIO-ERROR] No mixed audio found for slug={slug}.\n"
        f"Expected one of:\n"
        f"   {mix_wav}\n"
        f"   {mix_mp3}\n\n"
        f"Run 2_stems.py to generate the mix.\n"
    )
    sys.exit(1)


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
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from slug.")
    p.add_argument("--slug", required=True, help="Song slug, e.g. californication")
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
        "--offset",
        type=str,
        default="-1.5",
        help="Offset in seconds",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

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
                font_size_value = default_font_size
            else:
                try:
                    v = int(resp)
                    if 20 <= v <= 200:
                        font_size_value = v
                    else:
                        print(
                            f"Value {v} out of range; using default {default_font_size}"
                        )
                        font_size_value = default_font_size
                except ValueError:
                    print(
                        f"Invalid integer; using default font size {default_font_size}"
                    )
                    font_size_value = default_font_size
        else:
            font_size_value = default_font_size

    ui_font_size = max(20, min(200, font_size_value))
    ass_font_size = int(ui_font_size * ASS_FONT_MULTIPLIER)
    log(
        "FONT",
        f"Using UI font size {ui_font_size} (ASS Fontsize={ass_font_size})",
        CYAN,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log("MP4GEN", f"Slug={slug}", CYAN)
    audio_path = choose_audio(slug)
    out_mp4 = OUTPUT_DIR / f"{slug}.mp4"

    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0:
        log("DUR", f"Audio duration unknown or zero for {audio_path}", YELLOW)

    artist, title = read_meta(slug)
    timings = read_timings(slug)
    log("META", f'Artist="{artist}", Title="{title}", entries={len(timings)}', CYAN)

    # Per-render title card override (does NOT touch meta.json).
    title_card_lines = prompt_title_card_lines(slug, artist, title)

    ass_path = build_ass(
        slug,
        artist,
        title,
        timings,
        audio_duration,
        args.font_name,
        ass_font_size,
        title_card_lines,
    )

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
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    log("MP4", f"Wrote MP4 to {out_mp4} in {t1 - t0:6.2f} s", GREEN)

    print()
    print(f"{BOLD}{BLUE}MP4 generation complete:{RESET} {out_mp4}")
    print("What would you like to open?")
    print("  1 = output directory")
    print("  2 = MP4 file")
    print("  3 = both (dir then MP4)")
    print("  0 = none")

    try:
        choice = input("Choice [0–3]: ").strip()
    except EOFError:
        choice = "0"

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
