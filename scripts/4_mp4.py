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

# --- Ensure repo root is importable so we can use scripts.timings_io -------------
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.timings_io import load_timings_any  # type: ignore

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = REPO_ROOT
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
BOTTOM_BOX_HEIGHT_FRACTION = 0.20  # 20% of screen height
TOP_BAND_FRACTION = 1.0 - BOTTOM_BOX_HEIGHT_FRACTION

NEXT_LYRIC_TOP_MARGIN_PX = 50
NEXT_LYRIC_BOTTOM_MARGIN_PX = 50

DIVIDER_LINE_OFFSET_UP_PX = 0
DIVIDER_HEIGHT_PX = 0.25

DIVIDER_LEFT_MARGIN_PX = VIDEO_WIDTH * 0.035
DIVIDER_RIGHT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX

VERTICAL_OFFSET_FRACTION = 0.0
TITLE_EXTRA_OFFSET_FRACTION = -0.20

NEXT_LINE_FONT_SCALE = 0.35
NEXT_LABEL_FONT_SCALE = NEXT_LINE_FONT_SCALE * 0.45
NEXT_LABEL_TOP_MARGIN_PX = 10
NEXT_LABEL_LEFT_MARGIN_PX = DIVIDER_LEFT_MARGIN_PX + NEXT_LABEL_TOP_MARGIN_PX

FADE_IN_MS = 50
FADE_OUT_MS = 50

# =============================================================================
# COLOR / OPACITY CONSTANTS
# =============================================================================
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

# Font sizing
DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER = 1.5  # UI points -> ASS "Fontsize"

# Global lyrics timing offset in seconds.
# Negative => lyrics earlier (sooner); Positive => lyrics later.
# Default from env; can be overridden by --offset argument.
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "-0.5") or "-0.5")

# Music note logic
MUSIC_NOTE_CHARS = "♪♫♬♩♭♮"
MUSIC_NOTE_KEYWORDS = {"instrumental", "solo", "guitar solo", "piano solo"}

NOTE_GAP_THRESHOLD_SECS = 4.0  # min gap between end of one phrase and start of the next
NOTE_INSET_SEC = 1.0           # note window inset from gap edges
MIN_NOTE_DURATION = 0.50       # minimum duration for note overlay (seconds)


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
    # ASS format: H:MM:SS.cs
    if sec < 0:
        sec = 0.0
    total_cs = int(round(sec * 100))
    total_seconds, cs = divmod(total_cs, 100)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def rgb_to_bgr(rrggbb: str) -> str:
    s = (rrggbb or "").strip().lstrip("#")
    s = s.zfill(6)[-6:]
    rr = s[0:2]
    gg = s[2:4]
    bb = s[4:6]
    return f"{bb}{gg}{rr}"


def is_music_only(text: str) -> bool:
    """
    Heuristic: treat line as "music-only" when it looks like pure notes / non-words.
    """
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
    for kw in MUSIC_NOTE_KEYWORDS:
        if kw in lower:
            return True
    return False


def random_note_group() -> str:
    """
    Build a randomized note cluster:
      - length: 1–4 characters
      - chars: from MUSIC_NOTE_CHARS
    """
    length = random.randint(1, 4)
    return "".join(random.choice(MUSIC_NOTE_CHARS) for _ in range(length))


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
    Load timings via timings_io.load_timings_any which returns
        (line_index, start_secs, end_secs, text)

    We convert to:
        (start_secs, end_secs, text, line_index)
    and sort by start_secs.
    """
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    if not csv_path.exists():
        print(f"Timing CSV not found for slug={slug}: {csv_path}")
        sys.exit(1)

    native_rows = load_timings_any(csv_path)  # (line_index, start, end, text)
    rows = [(start, end, text, li) for (li, start, end, text) in native_rows]
    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {csv_path}", CYAN)
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


def offset_tag(val: float) -> str:
    s = f"{val:+.3f}".replace("-", "m").replace("+", "p").replace(".", "p")
    return f"_offset_{s}s"


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
    """
    Build ASS subtitles with:
      - Top-band lyrics
      - Bottom-band "Next:" preview
      - Music-note overlays in gaps >= NOTE_GAP_THRESHOLD_SECS

    `timings` must be a list of (start_secs, end_secs, text, line_index) in song time.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset_applied)}.ass"

    if audio_duration <= 0.0 and timings:
        # Fallback: extend a bit past the last line.
        last_end = max(end for (start, end, _text, _li) in timings)
        audio_duration = last_end + 5.0
    if audio_duration <= 0.0:
        audio_duration = 5.0

    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Geometry for top/bottom regions.
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_divider_nominal = top_band_height
    bottom_band_height = playresy - y_divider_nominal

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main_top = center_top + offset_px
    y_title = y_main_top + int(top_band_height * TITLE_EXTRA_OFFSET_FRACTION)

    x_center = playresx // 2
    y_center_full = playresy // 2

    line_y = max(0, y_divider_nominal - DIVIDER_LINE_OFFSET_UP_PX)

    inner_bottom_box_height = max(
        1, bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX
    )
    y_next = y_divider_nominal + NEXT_LYRIC_TOP_MARGIN_PX + inner_bottom_box_height // 2

    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))
    margin_v = 0

    # ASS color strings for top band.
    top_primary_ass = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    top_back_ass = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"
    secondary_ass = "&H000000FF"
    outline_ass = "&H00000000"
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
        text = text.replace("\\N", "\\N").replace("\n", r"\N")
        return text

    events: list[str] = []

    # Normalize timings into on-screen coordinates (offset applied, clamped).
    unified: list[tuple[float, float, str, int, bool]] = []
    for start_raw, end_raw, raw_text, line_index in timings:
        text = (raw_text or "").strip()
        if not text:
            continue

        start = start_raw + offset_applied
        end = end_raw + offset_applied

        # Drop lines that are entirely off-screen.
        if end <= 0:
            continue
        if audio_duration and start >= audio_duration:
            continue

        if audio_duration and end > audio_duration:
            end = audio_duration
        if end <= start:
            end = start + 0.01  # minimal duration

        music_only = is_music_only(text)
        unified.append((max(0.0, start), max(0.0, end), text, line_index, music_only))

    unified.sort(key=lambda x: x[0])

    if not unified:
        # No timings; just show title card for entire duration.
        title_lines = [line for line in (title, f"by {artist}" if artist else "") if line]
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

    first_lyric_time = max(0.0, unified[0][0])

    # Intro title / artist card.
    title_lines = []
    if title:
        title_lines.append(title)
    if artist:
        title_lines.append(f"by {artist}")
    if title_lines:
        title_end = min(first_lyric_time, 5.0) if first_lyric_time > 0.1 else first_lyric_time
        intro_block = "\\N".join(title_lines)
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(0.0),
                end=seconds_to_ass_time(title_end),
                text=f"{{\\an5\\pos({x_center},{y_title})}}{ass_escape(intro_block)}",
            )
        )

    fade_tag_main = ""
    if FADE_IN_MS > 0 or FADE_OUT_MS > 0:
        fade_tag_main = f"\\fad({int(FADE_IN_MS)},{int(FADE_OUT_MS)})"

    n = len(unified)

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

    for i, (start_i, end_i, raw_text, _li, music_i) in enumerate(unified):
        start = max(0.0, start_i)
        end = max(start, end_i)
        if end > audio_duration:
            end = audio_duration
        if end <= start:
            continue

        # Main lyric line (with fade).
        main_text = ass_escape(raw_text)
        y_for_line = (VIDEO_HEIGHT // 2) if music_i else y_main_top
        main_tag = f"{{\\an5\\pos({playresx // 2},{y_for_line}){fade_tag_main}}}"
        events.append(
            "Dialogue: 1,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=main_tag + main_text,
            )
        )

        # No next-line UI or notes if this is the last line.
        if i >= n - 1:
            continue

        next_start_raw, next_end_raw, next_text_raw, _li2, next_music = unified[i + 1]
        next_start = max(0.0, next_start_raw)
        if next_start <= start:
            next_start = min(audio_duration, end + 0.01)

        # Decide whether to place a note block in the gap AFTER this line.
        gap = next_start - end
        has_note_gap = False
        note_start = note_end = None

        if (
            gap >= NOTE_GAP_THRESHOLD_SECS
            and not music_i
            and not next_music
        ):
            # Candidate note window within the gap.
            ns = end + NOTE_INSET_SEC
            ne = next_start - NOTE_INSET_SEC
            if ne > audio_duration:
                ne = audio_duration
            if ns < 0.0:
                ns = 0.0

            if ne - ns >= MIN_NOTE_DURATION:
                # Double-check: ensure no lyric overlaps [ns, ne].
                safe = True
                for sj, ej, _t, _li_any, _music_any in unified:
                    if sj < ne and ej > ns:
                        safe = False
                        break
                if safe:
                    has_note_gap = True
                    note_start = ns
                    note_end = ne

                    note_text = ass_escape(random_note_group())
                    note_font = preview_font * 2

                    # Option 3: vertical jitter only (keep horizontal center).
                    jitter_px = int(VIDEO_HEIGHT * 0.02)
                    y_note = y_center_full + random.randint(-jitter_px, jitter_px)

                    note_tag = (
                        f"{{\\an5\\pos({playresx // 2},{y_note})"
                        f"\\fs{note_font}{fade_tag_main}}}"
                    )
                    events.append(
                        "Dialogue: 2,{start},{end},Default,,0,0,0,,{text}".format(
                            start=seconds_to_ass_time(note_start),
                            end=seconds_to_ass_time(note_end),
                            text=note_tag + note_text,
                        )
                    )

        # "Next:" preview & divider logic.
        # If there's a note gap after this line: hide preview entirely for that transition (your choice B).
        if has_note_gap:
            continue

        # Skip preview UI for music-only transitions; the line itself acts as the indicator.
        if music_i or next_music:
            continue

        if not next_text_raw:
            continue

        # Divider line (no fade).
        divider_tag = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color_bgr}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0\\p1}}"
        )
        divider_shape = (
            f"m {x_left} 0 l {x_right} 0 "
            f"l {x_right} {divider_height} l {x_left} {divider_height}{{\\p0}}"
        )
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(next_start),
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
                end=seconds_to_ass_time(next_start),
                text=label_tag + "Next:",
            )
        )

        # Next-lyric preview text (with fade).
        preview_text = ass_escape(next_text_raw)
        preview_tag = (
            f"{{\\an5\\pos({playresx // 2},{y_next})"
            f"\\fs{preview_font}"
            f"\\1c&H{next_color_bgr}&"
            f"\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&"
            f"{fade_tag_main}}}"
        )
        events.append(
            "Dialogue: 2,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(next_start),
                text=preview_tag + preview_text,
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
    p.add_argument("--font-size", type=int, help="Subtitle font size (20–200). Default 120.")
    p.add_argument("--font-name", type=str, default="Helvetica", help="Subtitle font name. Default Helvetica.")
    p.add_argument(
        "--offset",
        type=float,
        default=None,
        help="Global lyrics/text offset in seconds. Negative=sooner, Positive=later. Overrides KARAOKE_OFFSET_SECS.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-render even if the exact output MP4 already exists.",
    )
    return p.parse_args(argv)


def main(argv=None):
    global LYRICS_OFFSET_SECS

    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)
    profile = args.profile

    # Resolve effective offset: CLI flag wins; fallback to env.
    if args.offset is not None:
        LYRICS_OFFSET_SECS = float(args.offset)

    log("OFFSET", f"Applying global lyrics offset {LYRICS_OFFSET_SECS:+.3f}s (neg=sooner, pos=later)", CYAN)

    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(LYRICS_OFFSET_SECS)}.mp4"

    # Skip all work if target exists and not forcing.
    if out_mp4.exists() and not args.force:
        log("MP4", f"Exists, skipping render: {out_mp4.name}", YELLOW)
        print()
        print(f"{BOLD}{BLUE}MP4 already present:{RESET} {out_mp4}")
        print("What would you like to open?")
        print("  1 = output directory")
        print("  2 = MP4 file")
        print("  3 = both (dir then MP4)")
        print("  4 = upload to YouTube (private)")
        print("  0 = none")
        try:
            choice = input("Choice [0–4]: ").strip()
        except EOFError:
            choice = "0"
        if choice == "1":
            open_path(OUTPUT_DIR)
        elif choice == "2":
            open_path(out_mp4)
        elif choice == "3":
            open_path(OUTPUT_DIR)
            open_path(out_mp4)
        elif choice == "4":
            artist, title = read_meta(slug)
            default_title = None
            if artist or title:
                display = f"{artist} - {title}" if artist and title else (title or artist or slug)
                default_title = f"{display} ({profile}, offset {LYRICS_OFFSET_SECS:+.3f}s)"
            try:
                resp = input(
                    f'YouTube title [ENTER for default{" ("+default_title+")" if default_title else ""}]: '
                ).strip()
            except EOFError:
                resp = ""
            final_title = resp or (default_title or out_mp4.stem)
            cmd = [
                sys.executable,
                str(BASE_DIR / "scripts" / "5_upload.py"),
                "--file",
                str(out_mp4),
                "--title",
                final_title,
                # privacy default is private; 5_upload.py handles flags
            ]
            log("UPLOAD", " ".join(cmd), BLUE)
            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                log("UPLOAD", f"Upload failed (exit {e.returncode}).", RED)
        else:
            log("OPEN", "No open action selected.", YELLOW)
        return

    # Continue with full render path.
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
                        print(f"Value {v} out of range; using default {default_font_size}")
                        font_size_value = default_font_size
                except ValueError:
                    print(f"Invalid integer; using default font size {default_font_size}")
                    font_size_value = default_font_size
        else:
            font_size_value = default_font_size

    ui_font_size = max(20, min(200, font_size_value))
    ass_font_size = int(ui_font_size * ASS_FONT_MULTIPLIER)
    log("FONT", f"Using UI font size {ui_font_size} (ASS Fontsize={ass_font_size})", CYAN)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log("MP4GEN", f"Slug={slug}, profile={profile}", CYAN)

    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0:
        log("DUR", f"Audio duration unknown or zero for {audio_path}", YELLOW)

    artist, title = read_meta(slug)
    timings = read_timings(slug)  # (start,end,text,line_index)
    log("META", f'Artist="{artist}", Title="{title}", entries={len(timings)}', CYAN)

    ass_path = build_ass(
        slug, profile, artist, title, timings, audio_duration, args.font_name, ass_font_size, LYRICS_OFFSET_SECS
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
    print("  4 = upload to YouTube (private)")
    print("  0 = none")

    try:
        choice = input("Choice [0–4]: ").strip()
    except EOFError:
        choice = "0"

    if choice == "1":
        open_path(OUTPUT_DIR)
    elif choice == "2":
        open_path(out_mp4)
    elif choice == "3":
        open_path(OUTPUT_DIR)
        open_path(out_mp4)
    elif choice == "4":
        artist, title = read_meta(slug)
        default_title = None
        if artist or title:
            display = f"{artist} - {title}" if artist and title else (title or artist or slug)
            default_title = f"{display} ({profile}, offset {LYRICS_OFFSET_SECS:+.3f}s)"
        try:
            resp = input(
                f'YouTube title [ENTER for default{" ("+default_title+")" if default_title else ""}]: '
            ).strip()
        except EOFError:
            resp = ""
        final_title = resp or (default_title or out_mp4.stem)
        cmd_up = [
            sys.executable,
            str(BASE_DIR / "scripts" / "5_upload.py"),
            "--file",
            str(out_mp4),
            "--title",
            final_title,
        ]
        log("UPLOAD", " ".join(cmd_up), BLUE)
        try:
            subprocess.run(cmd_up, check=True)
        except subprocess.CalledProcessError as e:
            log("UPLOAD", f"Upload failed (exit {e.returncode}).", RED)
    else:
        log("OPEN", "No open action selected.", YELLOW)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
