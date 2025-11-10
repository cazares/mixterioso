#!/usr/bin/env python3
import argparse
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
#  0.50 = 50% of the main font size.
NEXT_LINE_FONT_SCALE = 0.35

# How big the "Next:" label text is relative to the main lyric text.
# By default this is smaller than the actual preview line.
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45

# Margins for the "Next:" label within the bottom box. The label is
# placed at the top-left corner of the bottom rectangle with these offsets.
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

# Fade timing (milliseconds) applied to each lyric change for both the main
# lyric and the preview lyric (and the bottom UI elements).
FADE_IN_MS = 250
FADE_OUT_MS = 250

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
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "0") or "0")
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

    # Work entirely in integer centiseconds to avoid rounding issues.
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
    """
    s = (rrggbb or "").strip().lstrip("#")
    s = s.zfill(6)[-6:]
    rr = s[0:2]
    gg = s[2:4]
    bb = s[4:6]
    return f"{bb}{gg}{rr}"


def is_music_only(text: str) -> bool:
    """
    Heuristic: True if the line is basically music notes / instrumental.
    """
    if not text:
        return False
    stripped = text.strip()
    lower = stripped.lower()

    for kw in MUSIC_NOTE_KEYWORDS:
        if kw in lower:
            return True

    contains_note_char = any(ch in stripped for ch in MUSIC_NOTE_CHARS)
    if not contains_note_char:
        return False

    import re

    cleaned = re.sub(r"[^\w]+", "", stripped)
    for ch in MUSIC_NOTE_CHARS:
        cleaned = cleaned.replace(ch, "")
    return cleaned == ""


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


def read_timings(slug: str) -> list[tuple[float, str, int]]:
    """
    Return list of (time_secs, text, line_index).

    Preferred CSV format:
        line_index,time_secs,text

    Fallback 2-column format:
        time_secs,text   (line_index is treated as 0 = normal lyric)
    """
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if not timing_path.exists():
        print(f"Timing CSV not found for slug={slug}: {timing_path}")
        sys.exit(1)

    rows: list[tuple[float, str, int]] = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        import csv

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


def build_ass(
    slug: str,
    artist: str,
    title: str,
    timings: list[tuple[float, str, int]],
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

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Geometry for top/bottom regions.
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_divider_nominal = top_band_height  # theoretical border between top and bottom boxes
    bottom_band_height = playresy - y_divider_nominal

    # Vertical positions for the top region.
    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_default = center_top + offset_px
    y_title = y_main_default + int(top_band_height * TITLE_EXTRA_OFFSET_FRACTION)

    x_center = playresx // 2
    y_center_full = playresy // 2

    # Divider line vertical position.
    line_y = max(0, y_divider_nominal - DIVIDER_LINE_OFFSET_UP_PX)

    # Next-lyric baseline position inside bottom box.
    inner_bottom_box_height = max(
        1, bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX
    )
    y_next = (
        y_divider_nominal
        + NEXT_LYRIC_TOP_MARGIN_PX
        + inner_bottom_box_height // 2
    )

    # Font sizes for next-lyric line and the "Next:" label.
    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))

    margin_v = 0

    # Precomputed ASS color strings for top band.
    top_primary_ass = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    top_back_ass = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"
    # Secondary, outline, etc.
    secondary_ass = "&H000000FF"  # red (unused for now)
    outline_ass = "&H00000000"    # black
    back_ass = top_back_ass

    header_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "Collisions: Normal",
        f"PlayResX: {playresx}",
        f"PlayResY: {playresy}",
        "ScaledBorderAndShadow: yes",
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

    def make_fad_tag() -> str:
        if FADE_IN_MS <= 0 and FADE_OUT_MS <= 0:
            return ""
        return f"\\fad({int(max(0, FADE_IN_MS))},{int(max(0, FADE_OUT_MS))})"

    fade_tag = make_fad_tag()

    events: list[str] = []

    # Normalize timings and filter out-of-range / empty lines.
    unified: list[tuple[float, str, int]] = []
    for t, text, line_index in timings:
        if t < 0 or (audio_duration and t > audio_duration):
            continue
        text = (text or "").strip()
        if not text:
            continue
        unified.append((t, text, line_index))

    unified.sort(key=lambda x: x[0])

    offset = LYRICS_OFFSET_SECS

    # If no timings, just show a centered title card for the whole song.
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
                text=f"{{\\an5\\pos({x_center},{y_center_full})}}{ass_escape(intro_block)}",
            )
        )

        ass_path.write_text("\n".join(header_lines + events) + "\n", encoding="utf-8")
        log("ASS", f"Wrote ASS subtitles (title only) to {ass_path}", GREEN)
        return ass_path

    first_lyric_time = max(0.0, unified[0][0] + offset)

    # Intro title / artist card, centered, with no lyrics / previews / divider.
    title_lines = []
    if title:
        title_lines.append(title)
    if artist:
        title_lines.append(f"by {artist}")

    if title_lines:
        if first_lyric_time > 0.1:
            title_end = min(first_lyric_time, 5.0)
        else:
            title_end = first_lyric_time

        intro_block = "\\N".join(title_lines)
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(0.0),
                end=seconds_to_ass_time(title_end),
                text=f"{{\\an5\\pos({x_center},{y_center_full})}}{ass_escape(intro_block)}",
            )
        )

    # Build per-line events.
    n = len(unified)
    next_color_bgr = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color_bgr = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color_bgr = rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    for i, (t, raw_text, _line_index) in enumerate(unified):
        start = max(0.0, t + offset)
        if i < n - 1:
            end = max(start, unified[i + 1][0] + offset)
        else:
            end = audio_duration or (start + 5.0)

        if end > audio_duration:
            end = audio_duration
        if end <= start:
            continue

        is_music = is_music_only(raw_text)

        # Main line (lyric or note).
        y_main = y_center_full if is_music else y_main_default
        main_text = ass_escape(raw_text)
        main_tag = f"{{\\an5\\pos({x_center},{y_main}){fade_tag}}}"
        events.append(
            "Dialogue: 1,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=main_tag + main_text,
            )
        )

        # For last line or instrumental lines, hide the bottom UI entirely.
        is_last = i == n - 1
        if is_music or is_last:
            continue

        # Preview text: next line, unless that line is "music-only".
        preview_text_raw = unified[i + 1][1] if i < n - 1 else ""
        if preview_text_raw and not is_music_only(preview_text_raw):
            preview_text = ass_escape(preview_text_raw)
            preview_tag = (
                f"{{\\an5\\pos({x_center},{y_next})"
                f"\\fs{preview_font}"
                f"\\1c&H{next_color_bgr}&"
                f"\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&"
                f"{fade_tag}}}"
            )
            events.append(
                "Dialogue: 2,{start},{end},Default,,0,0,0,,{text}".format(
                    start=seconds_to_ass_time(start),
                    end=seconds_to_ass_time(end),
                    text=preview_tag + preview_text,
                )
            )

        # Divider line for this interval.
        divider_height = max(0.5, float(DIVIDER_HEIGHT_PX))
        left_margin = max(0.0, float(DIVIDER_LEFT_MARGIN_PX))
        right_margin = max(0.0, float(DIVIDER_RIGHT_MARGIN_PX))
        x_left = left_margin
        x_right = playresx - right_margin
        if x_right <= x_left:
            x_left = 0.0
            x_right = float(playresx)

        divider_tag = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color_bgr}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0"
            f"{fade_tag}"
            f"\\p1}}"
        )
        divider_shape = (
            f"m {x_left} 0 l {x_right} 0 l {x_right} {divider_height} "
            f"l {x_left} {divider_height}{{\\p0}}"
        )
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=divider_tag + divider_shape,
            )
        )

        # "Next:" label in upper-left of bottom rectangle during this interval.
        label_x = NEXT_LABEL_LEFT_MARGIN_PX
        label_y = y_divider_nominal + NEXT_LABEL_TOP_MARGIN_PX
        label_tag = (
            f"{{\\an7\\pos({label_x},{label_y})"
            f"\\fs{next_label_font}"
            f"\\1c&H{next_label_color_bgr}&"
            f"\\1a&H{NEXT_LABEL_ALPHA_HEX}&"
            f"{fade_tag}}}"
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
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    mix_mp3 = MIXES_DIR / f"{slug}_{profile}.mp3"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    if profile == "lyrics":
        audio_path = mp3_path
        if not audio_path.exists():
            print(f"Audio mp3 not found for slug={slug}: {audio_path}")
            sys.exit(1)
        print(f"[AUDIO] Using original mp3 for profile=lyrics: {audio_path}")
        return audio_path

    if mix_wav.exists():
        print(f"[AUDIO] Using mixed WAV for profile={profile}: {mix_wav}")
        return mix_wav

    if mix_mp3.exists():
        print(f"[AUDIO] Using mixed MP3 for profile={profile}: {mix_mp3}")
        return mix_mp3

    if mp3_path.exists():
        print(
            f"[AUDIO] Mixed WAV/MP3 for profile={profile} not found.\n"
            f"        Falling back to original mp3: {mp3_path}"
        )
        return mp3_path

    print(
        f"Audio not found for slug={slug}, profile={profile}.\n"
        f"Tried:\n"
        f"  mix wav: {mix_wav}\n"
        f"  mix mp3: {mix_mp3}\n"
        f"  mp3: {mp3_path}"
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

    log("MP4GEN", f"Slug={slug}, profile={profile}", CYAN)

    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0:
        log("DUR", f"Audio duration unknown or zero for {audio_path}", YELLOW)

    artist, title = read_meta(slug)
    timings = read_timings(slug)
    log("META", f'Artist="{artist}", Title="{title}", entries={len(timings)}', CYAN)

    ass_path = build_ass(
        slug, artist, title, timings, audio_duration, args.font_name, ass_font_size
    )

    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}.mp4"

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
