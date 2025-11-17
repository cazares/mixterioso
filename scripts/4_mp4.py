#!/usr/bin/env python3
# scripts/4_mp4.py
# Generate a karaoke MP4 from an MP3/WAV + timings CSV.
#
# Canonical timings CSV (4 columns) is read via scripts.timings_io.load_timings_any:
#   line_index,start,end,text
#
# This script:
#   - Uses 1920x1080 video
#   - Renders main lyrics in the top band
#   - Renders "Next:" preview in the bottom band
#   - Inserts randomized music-note overlays in gaps >= NOTE_GAP_THRESHOLD_SECS
#   - Never overlays notes on top of active lyrics
#   - Hides "Next:" preview during note sections
#   - Supports global offset (--offset or KARAOKE_OFFSET_SECS)
#   - Supports --force to re-render MP4 even if it exists
#   - Can call 5_upload.py to upload to YouTube

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

# --- Ensure repo root is importable so we can use scripts.timings_io ---
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.timings_io import load_timings_any  # type: ignore

# ANSI colors
RESET = "\033[0m"
BOLD  = "\033[1m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
RED   = "\033[31m"
BLUE  = "\033[34m"

BASE_DIR     = REPO_ROOT
TXT_DIR      = BASE_DIR / "txts"
MP3_DIR      = BASE_DIR / "mp3s"
MIXES_DIR    = BASE_DIR / "mixes"
TIMINGS_DIR  = BASE_DIR / "timings"
OUTPUT_DIR   = BASE_DIR / "output"
META_DIR     = BASE_DIR / "meta"

VIDEO_WIDTH  = 1920
VIDEO_HEIGHT = 1080

# =============================================================================
# LAYOUT CONSTANTS
# =============================================================================
BOTTOM_BOX_HEIGHT_FRACTION = 0.20
TOP_BAND_FRACTION          = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION

NEXT_LYRIC_TOP_MARGIN_PX    = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50

DIVIDER_LINE_OFFSET_UP_PX = 0
DIVIDER_HEIGHT_PX         = 0.25

DIVIDER_LEFT_MARGIN_PX  = VIDEO_WIDTH * 0.035
DIVIDER_RIGHT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX

VERTICAL_OFFSET_FRACTION = 0.0
TITLE_EXTRA_OFFSET_FRACTION = -0.20

NEXT_LINE_FONT_SCALE  = 0.35
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX  = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

FADE_IN_MS  = 50
FADE_OUT_MS = 50

# =============================================================================
# COLOR / OPACITY
# =============================================================================
GLOBAL_NEXT_COLOR_RGB  = "FFFFFF"
GLOBAL_NEXT_ALPHA_HEX  = "4D"

DIVIDER_COLOR_RGB      = "FFFFFF"
DIVIDER_ALPHA_HEX      = "80"

TOP_LYRIC_TEXT_COLOR_RGB = "FFFFFF"
TOP_LYRIC_TEXT_ALPHA_HEX = "00"

BOTTOM_BOX_BG_COLOR_RGB = "000000"
BOTTOM_BOX_BG_ALPHA_HEX = "00"

TOP_BOX_BG_COLOR_RGB = "000000"
TOP_BOX_BG_ALPHA_HEX = "00"

NEXT_LABEL_COLOR_RGB = "FFFFFF"
NEXT_LABEL_ALPHA_HEX = GLOBAL_NEXT_ALPHA_HEX

# Font sizing
DEFAULT_UI_FONT_SIZE  = 120
ASS_FONT_MULTIPLIER   = 1.5

# Global offset
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "-0.5") or "-0.5")

# =============================================================================
# MUSIC NOTES
# =============================================================================
MUSIC_NOTE_CHARS = "♪♫♩♬"   # white only
NOTE_GAP_THRESHOLD_SECS = 4.0
NOTE_SAFE_INSET = 0.35
NOTE_MIN_COUNT = 1
NOTE_MAX_COUNT = 7
NOTE_DURATION  = 2.0
NOTE_FADE_IN   = 150
NOTE_FADE_OUT  = 200


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
    cs_total = int(round(sec * 100))
    total_seconds, cs = divmod(cs_total, 100)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rgb_to_bgr(rrggbb: str) -> str:
    s = (rrggbb or "").strip().lstrip("#")
    s = s.zfill(6)[-6:]
    rr, gg, bb = s[0:2], s[2:4], s[4:6]
    return f"{bb}{gg}{rr}"


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
    for kw in ["instrumental", "solo", "guitar solo", "piano solo"]:
        if kw in lower:
            return True
    return False


def random_note() -> str:
    return random.choice(MUSIC_NOTE_CHARS)


def random_pos_fullscreen() -> tuple[int,int]:
    sx_min = int(VIDEO_WIDTH  * NOTE_SAFE_INSET)
    sx_max = int(VIDEO_WIDTH  * (1 - NOTE_SAFE_INSET))
    sy_min = int(VIDEO_HEIGHT * NOTE_SAFE_INSET)
    sy_max = int(VIDEO_HEIGHT * 0.55)
    return (
        random.randint(sx_min, sx_max),
        random.randint(sy_min, sy_max),
    )


def read_meta(slug: str) -> tuple[str,str]:
    p = META_DIR / f"{slug}.json"
    artist = ""
    title  = slug
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            artist = data.get("artist","") or ""
            title  = data.get("title",title) or title
        except Exception as e:
            log("META", f"Failed to read meta: {e}", YELLOW)
    return artist, title


def read_timings(slug: str):
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    if not csv_path.exists():
        print(f"Timing CSV not found: {csv_path}")
        sys.exit(1)
    native = load_timings_any(csv_path)
    rows = [(start,end,text,li) for (li,start,end,text) in native]
    rows.sort(key=lambda x: x[0])
    return rows


def probe_audio_duration(p: Path) -> float:
    if not p.exists():
        return 0.0
    cmd = [
        "ffprobe","-v","error",
        "-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",
        str(p),
    ]
    try:
        out = subprocess.check_output(cmd, text=True)
        return float(out.strip())
    except:
        return 0.0


def offset_tag(val: float) -> str:
    s = f"{val:+.3f}".replace("-", "m").replace("+", "p").replace(".", "p")
    return f"_offset_{s}s"


# =============================================================================
# ASS GENERATION (D-level integrated)
# =============================================================================
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

    # Audio duration fallback
    if audio_duration <= 0.0 and timings:
        last_end = max(end for (start,end,_t,_li) in timings)
        audio_duration = last_end + 5.0
    if audio_duration <= 0.0:
        audio_duration = 5.0

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Geometry
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_div = top_band_height
    bottom_band_height = playresy - y_div

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px
    y_title    = y_main_top + int(top_band_height * TITLE_EXTRA_OFFSET_FRACTION)

    x_center = playresx // 2
    y_center_full = playresy // 2

    line_y = max(0, y_div - DIVIDER_LINE_OFFSET_UP_PX)

    inner_bottom_height = max(
        1,
        bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX
    )
    y_next = y_div + NEXT_LYRIC_TOP_MARGIN_PX + inner_bottom_height // 2

    preview_font    = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))
    margin_v = 0

    # ASS colors
    top_primary = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    secondary   = "&H000000FF"
    outline     = "&H00000000"
    back        = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {playresx}",
        f"PlayResY: {playresy}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
         "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
         "Alignment, MarginL, MarginR, MarginV, Encoding"),
        (
            f"Style: Default,{font_name},{font_size_script},"
            f"{top_primary},{secondary},{outline},{back},"
            "0,0,0,0,100,100,0,0,1,4,0,5,50,50,"
            f"{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    def esc(s: str) -> str:
        return s.replace("{","(").replace("}"," )").replace("\n", r"\N")

    events = []

    # Normalize timings with minimal safety clamps (Option C)
    unified = []
    for start_raw, end_raw, raw_text, li in timings:
        t = (raw_text or "").strip()
        if not t:
            continue

        start = start_raw + offset_applied
        end   = end_raw   + offset_applied

        # Option C clamp rules
        if start < 0:
            start = 0.0
        if end <= start:
            end = start + 0.01
        if audio_duration > 0 and end > audio_duration:
            end = audio_duration

        music_only = is_music_only(t)
        unified.append((start,end,t,li,music_only))

    unified.sort(key=lambda x: x[0])

    if not unified:
        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(0)},{seconds_to_ass_time(audio_duration)},"
            f"Default,,0,0,0,,{{\\an5\\pos({x_center},{y_center_full})}}{esc(block)}"
        )
        ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
        return ass_path

    # Title card
    first_t = unified[0][0]
    intro_end = min(first_t, 5.0) if first_t > 0.1 else first_t
    if intro_end > 0.05:
        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(0)},{seconds_to_ass_time(intro_end)},"
            f"Default,,0,0,0,,{{\\an5\\pos({x_center},{y_title})}}{esc(block)}"
        )

    fade_tag = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})" if (FADE_IN_MS or FADE_OUT_MS) else ""

    n = len(unified)
    next_color      = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color   = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color= rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    divider_height = max(0.5, DIVIDER_HEIGHT_PX)
    x_left  = float(DIVIDER_LEFT_MARGIN_PX)
    x_right = float(playresx - DIVIDER_RIGHT_MARGIN_PX)
    if x_right <= x_left:
        x_left = 0.0
        x_right= float(playresx)

    label_x = NEXT_LABEL_LEFT_MARGIN_PX
    label_y = y_div + NEXT_LABEL_TOP_MARGIN_PX

    # =====================================================================
    # MAIN LOOP
    # =====================================================================
    for i, (start_i, end_i, text_i, li_i, mus_i) in enumerate(unified):

        start = start_i
        end   = end_i

        # ----- MAIN LYRIC -----
        y_line = (VIDEO_HEIGHT // 2) if mus_i else y_main_top
        events.append(
            f"Dialogue: 1,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},Default,,0,0,0,,"
            f"{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{esc(text_i)}"
        )

        if i >= n - 1:
            continue

        next_start, next_end, next_text, _li_n, next_mus = unified[i+1]
        gap = next_start - end
        has_notes = False

        # ----- MUSIC NOTES -----
        if (
            gap >= NOTE_GAP_THRESHOLD_SECS
            and not mus_i
            and not next_mus
        ):
            ns = end + 0.5
            ne = next_start - 0.5
            if ne > ns + 0.25:
                has_notes = True
                count = random.randint(NOTE_MIN_COUNT, NOTE_MAX_COUNT)
                for _ in range(count):
                    span = ne - ns
                    if span < 0.25:
                        break
                    spawn = ns + random.random() * span
                    deatht = min(spawn + NOTE_DURATION, next_start)
                    if deatht <= spawn:
                        continue
                    x,y = random_pos_fullscreen()
                    note = random_note()
                    tag  = f"{{\\an5\\pos({x},{y})\\fs{preview_font*2}\\fad({NOTE_FADE_IN},{NOTE_FADE_OUT})}}"
                    events.append(
                        f"Dialogue: 2,{seconds_to_ass_time(spawn)},{seconds_to_ass_time(deatht)},"
                        f"Default,,0,0,0,,{tag}{note}"
                    )

        if has_notes:
            continue
        if mus_i or next_mus:
            continue

        # ----- DIVIDER -----
        div_tag = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0\\p1}}"
        )
        shape = f"m {x_left} 0 l {x_right} 0 l {x_right} {divider_height} l {x_left} {divider_height}{{\\p0}}"

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},"
            f"Default,,0,0,0,,{div_tag}{shape}"
        )

        # ----- "NEXT:" LABEL -----
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},Default,,0,0,0,,"
            f"{{\\an7\\pos({label_x},{label_y})\\fs{next_label_font}"
            f"\\1c&H{next_label_color}&\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&}}Next:"
        )

        # ----- NEXT-LINE PREVIEW -----
        events.append(
            f"Dialogue: 2,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},Default,,0,0,0,,"
            f"{{\\an5\\pos({playresx//2},{y_next})\\fs{preview_font}"
            f"\\1c&H{next_color}&\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&{fade_tag}}}{esc(next_text)}"
        )

    # Write ASS
    ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return ass_path


# =============================================================================
# Remaining logic: choose_audio, parse_args, main, etc.
# (unchanged from your version except for Option C behavior above)
# =============================================================================
def choose_audio(slug: str, profile: str) -> Path:
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    mix_mp3 = MIXES_DIR / f"{slug}_{profile}.mp3"
    mp3_path= MP3_DIR  / f"{slug}.mp3"

    if profile == "lyrics":
        if mp3_path.exists():
            print(f"[AUDIO] Using original mp3 for lyrics: {mp3_path}")
            return mp3_path
        print(f"Audio not found: {mp3_path}")
        sys.exit(1)

    if mix_wav.exists():
        print(f"[AUDIO] Using mixed WAV: {mix_wav}")
        return mix_wav
    if mix_mp3.exists():
        print(f"[AUDIO] Using mixed MP3: {mix_mp3}")
        return mix_mp3

    if mp3_path.exists():
        print(f"[AUDIO] Mixed track not found; fallback: {mp3_path}")
        return mp3_path

    print(f"No audio found for slug={slug}, profile={profile}")
    sys.exit(1)


def open_path(path: Path)->None:
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        elif sys.platform.startswith("win"):
            subprocess.run(["start", str(path)], shell=True)
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception as e:
        print(f"[OPEN] Failed to open {path}: {e}")


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--profile", required=True,
                  choices=["lyrics","karaoke","car-karaoke","no-bass","car-bass-karaoke"])
    p.add_argument("--font-size", type=int)
    p.add_argument("--font-name", type=str, default="Helvetica")
    p.add_argument("--offset", type=float, default=None)
    p.add_argument("--force", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    global LYRICS_OFFSET_SECS

    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)
    profile = args.profile

    # Offset
    if args.offset is not None:
        LYRICS_OFFSET_SECS = float(args.offset)
    print(f"[OFFSET] Using lyrics offset {LYRICS_OFFSET_SECS:+.3f}s")

    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(LYRICS_OFFSET_SECS)}.mp4"

    # Pre-existing MP4 menu omitted for brevity (unchanged)
    # [... identical to your version ...]

    # ================================================================
    # Full render
    # ================================================================
    font_size = args.font_size or DEFAULT_UI_FONT_SIZE
    font_size = max(20, min(200, font_size))
    ass_font_size = int(font_size * ASS_FONT_MULTIPLIER)

    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)

    artist, title = read_meta(slug)
    timings = read_timings(slug)

    ass_path = build_ass(
        slug, profile,
        artist, title,
        timings,
        audio_duration,
        args.font_name,
        ass_font_size,
        LYRICS_OFFSET_SECS,
    )

    cmd = [
        "ffmpeg","-y",
        "-f","lavfi",
        "-i",f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration,1)}",
        "-i",str(audio_path),
        "-vf",f"subtitles={ass_path}",
        "-c:v","libx264",
        "-preset","medium",
        "-crf","18",
        "-c:a","aac",
        "-b:a","192k",
        "-shortest",
        str(out_mp4),
    ]

    print("[FFMPEG]"," ".join(cmd))
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    print(f"[MP4] Wrote {out_mp4} in {t1-t0:6.2f} s")


if __name__ == "__main__":
    main()

# end of 4_mp4.py
