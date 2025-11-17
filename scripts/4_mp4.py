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
ASS_FONT_MULTIPLIER = 1.5

# Global lyrics timing offset in seconds.
LYRICS_OFFSET_SECS = float(os.getenv("KARAOKE_OFFSET_SECS", "-0.5") or "-0.5")

# =============================================================================
# MUSIC NOTE LOGIC
# =============================================================================

MUSIC_NOTE_CHARS = "♪♫♩♬"  # white text only
NOTE_GAP_THRESHOLD_SECS = 4.0     # gaps >= 4s trigger note generation
NOTE_SAFE_INSET = 0.35            # avoid edges of screen
NOTE_MIN_COUNT = 1
NOTE_MAX_COUNT = 7
NOTE_DURATION = 2.0               # each note lives 2 seconds
NOTE_FADE_IN = 150                # ms
NOTE_FADE_OUT = 200               # ms


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


def random_pos_fullscreen() -> tuple[int, int]:
    """Safe-random position inside full screen, avoiding top/bottom lyric areas."""
    safe_x_min = int(VIDEO_WIDTH * NOTE_SAFE_INSET)
    safe_x_max = int(VIDEO_WIDTH * (1 - NOTE_SAFE_INSET))
    safe_y_min = int(VIDEO_HEIGHT * NOTE_SAFE_INSET)
    safe_y_max = int(VIDEO_HEIGHT * 0.55)  # keep above bottom-band
    return (
        random.randint(safe_x_min, safe_x_max),
        random.randint(safe_y_min, safe_y_max),
    )


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
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    if not csv_path.exists():
        print(f"Timing CSV not found for slug={slug}: {csv_path}")
        sys.exit(1)
    native = load_timings_any(csv_path)
    rows = [(start, end, text, li) for (li, start, end, text) in native]
    rows.sort(key=lambda x: x[0])
    return rows


def probe_audio_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return float(out.strip())
    except:
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
    Full ASS generation:
      • Top-band lyrics
      • Bottom-band next-line preview
      • Chaotic music notes in large gaps
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(offset_applied)}.ass"

    if audio_duration <= 0.0 and timings:
        last_end = max(end for (start, end, _t, _li) in timings)
        audio_duration = last_end + 5.0
    if audio_duration <= 0.0:
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
    y_title = y_main_top + int(top_band_height * TITLE_EXTRA_OFFSET_FRACTION)

    x_center = playresx // 2
    y_center_full = playresy // 2

    line_y = max(0, y_divider_nominal - DIVIDER_LINE_OFFSET_UP_PX)

    inner_bottom_height = max(
        1,
        bottom_band_height - NEXT_LYRIC_TOP_MARGIN_PX - NEXT_LYRIC_BOTTOM_MARGIN_PX
    )
    y_next = y_divider_nominal + NEXT_LYRIC_TOP_MARGIN_PX + inner_bottom_height // 2

    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    next_label_font = max(1, int(font_size_script * NEXT_LABEL_FONT_SCALE))
    margin_v = 0

    # ASS colors
    top_primary = f"&H{TOP_LYRIC_TEXT_ALPHA_HEX}{rgb_to_bgr(TOP_LYRIC_TEXT_COLOR_RGB)}"
    secondary = "&H000000FF"
    outline = "&H00000000"
    back = f"&H{TOP_BOX_BG_ALPHA_HEX}{rgb_to_bgr(TOP_BOX_BG_COLOR_RGB)}"

    header = [
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
            f"{top_primary},{secondary},{outline},{back},"
            "0,0,0,0,100,100,0,0,1,4,0,5,50,50,"
            f"{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Helper
    def esc(s: str) -> str:
        return s.replace("{", "(").replace("}", ")").replace("\n", r"\N")

    events: list[str] = []

    # Normalize timings with offset applied
    unified = []
    for start_raw, end_raw, raw_text, li in timings:
        t = (raw_text or "").strip()
        if not t:
            continue

        start = start_raw + offset_applied
        end = end_raw + offset_applied

        if end <= 0:
            continue
        if audio_duration and start >= audio_duration:
            continue
        end = min(end, audio_duration)
        if end <= start:
            end = start + 0.01

        music_only = is_music_only(t)
        unified.append((max(0, start), max(0, end), t, li, music_only))

    unified.sort(key=lambda x: x[0])

    if not unified:
        block = "\\N".join([
            title or "No lyrics",
            f"by {artist}" if artist else ""
        ])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(0)},{seconds_to_ass_time(audio_duration)},Default,,0,0,0,,"
            f"{{\\an5\\pos({x_center},{y_center_full})}}{esc(block)}"
        )
        ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
        return ass_path

    # Intro title
    first_t = unified[0][0]
    intro_end = min(first_t, 5.0) if first_t > 0.1 else first_t
    if intro_end > 0.05:
        block = "\\N".join([title, f"by {artist}"] if artist else [title])
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(0)},{seconds_to_ass_time(intro_end)},Default,,0,0,0,,"
            f"{{\\an5\\pos({x_center},{y_title})}}{esc(block)}"
        )

    fade_tag = f"\\fad({FADE_IN_MS},{FADE_OUT_MS})" if (FADE_IN_MS or FADE_OUT_MS) else ""

    n = len(unified)

    next_color = rgb_to_bgr(GLOBAL_NEXT_COLOR_RGB)
    divider_color = rgb_to_bgr(DIVIDER_COLOR_RGB)
    next_label_color = rgb_to_bgr(NEXT_LABEL_COLOR_RGB)

    divider_height = max(0.5, DIVIDER_HEIGHT_PX)
    left_margin = DIVIDER_LEFT_MARGIN_PX
    right_margin = DIVIDER_RIGHT_MARGIN_PX

    x_left = float(left_margin)
    x_right = float(playresx - right_margin)
    if x_right <= x_left:
        x_left = 0.0
        x_right = float(playresx)

    label_x = NEXT_LABEL_LEFT_MARGIN_PX
    label_y = y_divider_nominal + NEXT_LABEL_TOP_MARGIN_PX

    # ============================================================
    # MAIN LOOP — lyrics + preview + chaotic notes
    # ============================================================

    for i, (start_i, end_i, text_i, li_i, mus_i) in enumerate(unified):
        start = start_i
        end = end_i

        # ------------------------------
        # MAIN LYRIC
        # ------------------------------
        y_line = (VIDEO_HEIGHT // 2) if mus_i else y_main_top
        events.append(
            f"Dialogue: 1,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},Default,,0,0,0,,"
            f"{{\\an5\\pos({playresx//2},{y_line}){fade_tag}}}{esc(text_i)}"
        )

        # Last line → no next/notes
        if i >= n - 1:
            continue

        next_start_raw, next_end_raw, next_text_raw, _li_n, next_mus = unified[i + 1]
        next_start = max(0, next_start_raw)

        # Compute gap
        gap = next_start - end
        has_notes = False

        # ============================================================
        # CHAOTIC NOTE GENERATION
        # ============================================================
        if (
            gap >= NOTE_GAP_THRESHOLD_SECS
            and not mus_i
            and not next_mus
        ):
            ns = end + 0.5
            ne = next_start - 0.5
            if ne > ns + 0.25:
                has_notes = True

                # random number of notes
                count = random.randint(NOTE_MIN_COUNT, NOTE_MAX_COUNT)

                for _ in range(count):
                    span = ne - ns
                    if span < 0.25:
                        break

                    spawn = ns + random.random() * span
                    deatht = min(spawn + NOTE_DURATION, next_start)

                    if deatht <= spawn:
                        continue

                    # safe-random position
                    x, y = random_pos_fullscreen()

                    note = random_note()
                    tag = f"{{\\an5\\pos({x},{y})\\fs{preview_font*2}\\fad({NOTE_FADE_IN},{NOTE_FADE_OUT})}}"

                    events.append(
                        f"Dialogue: 2,{seconds_to_ass_time(spawn)},{seconds_to_ass_time(deatht)},"
                        f"Default,,0,0,0,,{tag}{note}"
                    )

        # During note sections → preview hidden
        if has_notes:
            continue

        # Skip preview during music-only transitions
        if mus_i or next_mus:
            continue

        # Divider line
        div_tag = (
            f"{{\\an7\\pos(0,{line_y})"
            f"\\1c&H{divider_color}&"
            f"\\1a&H{DIVIDER_ALPHA_HEX}&"
            f"\\bord0\\shad0\\p1}}"
        )
        shape = f"m {x_left} 0 l {x_right} 0 l {x_right} {divider_height} l {x_left} {divider_height}{{\\p0}}"

        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},Default,,0,0,0,,"
            f"{div_tag}{shape}"
        )

        # “Next:” label
        events.append(
            f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},Default,,0,0,0,,"
            f"{{\\an7\\pos({label_x},{label_y})\\fs{next_label_font}"
            f"\\1c&H{next_label_color}&\\1a&H{NEXT_LABEL_ALPHA_HEX}&}}Next:"
        )

        # Next line preview
        events.append(
            f"Dialogue: 2,{seconds_to_ass_time(start)},{seconds_to_ass_time(next_start)},Default,,0,0,0,,"
            f"{{\\an5\\pos({playresx//2},{y_next})\\fs{preview_font}"
            f"\\1c&H{next_color}&\\1a&H{GLOBAL_NEXT_ALPHA_HEX}&{fade_tag}}}{esc(next_text_raw)}"
        )

    # ------------------------------
    # WRITE ASS
    # ------------------------------
    ass_path.write_text("\n".join(header + events) + "\n", encoding="utf-8")
    return ass_path
def choose_audio(slug: str, profile: str) -> Path:
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
    mix_mp3 = MIXES_DIR / f"{slug}_{profile}.mp3"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    if profile == "lyrics":
        if mp3_path.exists():
            print(f"[AUDIO] Using original mp3 for profile=lyrics: {mp3_path}")
            return mp3_path
        print(f"Audio not found for slug={slug}: {mp3_path}")
        sys.exit(1)

    if mix_wav.exists():
        print(f"[AUDIO] Using mixed WAV: {mix_wav}")
        return mix_wav

    if mix_mp3.exists():
        print(f"[AUDIO] Using mixed MP3: {mix_mp3}")
        return mix_mp3

    if mp3_path.exists():
        print(f"[AUDIO] Mixed track not found; falling back to original {mp3_path}")
        return mp3_path

    print(f"No audio found for slug={slug}, profile={profile}")
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
        print(f"[OPEN] Failed to open {path}: {e}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate karaoke MP4 from slug/profile.")
    p.add_argument("--slug", required=True, help="Song slug")
    p.add_argument(
        "--profile",
        required=True,
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
    )
    p.add_argument("--font-size", type=int, help="Subtitle font size")
    p.add_argument("--font-name", type=str, default="Helvetica")
    p.add_argument("--offset", type=float, default=None)
    p.add_argument("--force", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    global LYRICS_OFFSET_SECS

    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)
    profile = args.profile

    # Resolve offset
    if args.offset is not None:
        LYRICS_OFFSET_SECS = float(args.offset)
    print(f"[OFFSET] Using lyrics offset {LYRICS_OFFSET_SECS:+.3f}s")

    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}{offset_tag(LYRICS_OFFSET_SECS)}.mp4"

    # If MP4 exists and not --force → show menu
    if out_mp4.exists() and not args.force:
        print()
        print(f"MP4 already exists: {out_mp4}")
        print("Open?")
        print("  1 = output directory")
        print("  2 = MP4")
        print("  3 = both")
        print("  4 = upload to YouTube")
        print("  0 = none")
        print("  5 = FORCE REGENERATE MP4")

        try:
            choice = input("Choice [0–5]: ").strip()
        except EOFError:
            choice = "0"

        if choice == "1":
            open_path(OUTPUT_DIR)
            return
        elif choice == "2":
            open_path(out_mp4)
            return
        elif choice == "3":
            open_path(OUTPUT_DIR)
            open_path(out_mp4)
            return
        elif choice == "4":
            # Pass through to upload script
            meta_artist, meta_title = read_meta(slug)
            default_title = None
            if meta_artist or meta_title:
                display = (
                    f"{meta_artist} - {meta_title}"
                    if (meta_artist and meta_title)
                    else (meta_title or meta_artist or slug)
                )
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
            print("[UPLOAD]", " ".join(cmd_up))
            try:
                subprocess.run(cmd_up, check=True)
            except subprocess.CalledProcessError as e:
                print(f"[UPLOAD] Failed ({e.returncode})")
            return

        elif choice == "5":
            # ============================================
            # FORCE-REGENERATE → Relaunch self with --force
            # ============================================
            print("[REGEN] Forcing MP4 regeneration…")
            new_cmd = [
                sys.executable,
                str(Path(__file__)),
                "--slug", slug,
                "--profile", profile,
                "--offset", str(LYRICS_OFFSET_SECS),
                "--force",
            ]
            print("[REGEN CMD]", " ".join(new_cmd))
            subprocess.run(new_cmd, check=True)
            return

        else:
            print("[OPEN] No action.")
            return

    # ================================================================
    # FULL RENDER
    # ================================================================
    font_size = args.font_size or DEFAULT_UI_FONT_SIZE
    font_size = max(20, min(200, font_size))
    ass_font_size = int(font_size * ASS_FONT_MULTIPLIER)

    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)

    artist, title = read_meta(slug)
    timings = read_timings(slug)

    ass_path = build_ass(
        slug,
        profile,
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
        "-f", "lavfi",
        "-i", f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration,1)}",
        "-i", str(audio_path),
        "-vf", f"subtitles={ass_path}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_mp4),
    ]

    print("[FFMPEG]", " ".join(cmd))
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()
    print(f"[MP4] Wrote {out_mp4} in {t1 - t0:6.2f} s")

    print()
    print("Open?")
    print("  1 = output directory")
    print("  2 = MP4")
    print("  3 = both")
    print("  4 = upload to YouTube")
    print("  0 = none")
    print("  5 = FORCE REGENERATE MP4")

    try:
        choice = input("Choice [0–5]: ").strip()
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
            disp = (
                f"{artist} - {title}"
                if (artist and title)
                else (title or artist or slug)
            )
            default_title = f"{disp} ({profile}, offset {LYRICS_OFFSET_SECS:+.3f}s)"
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
        print("[UPLOAD]", " ".join(cmd_up))
        try:
            subprocess.run(cmd_up, check=True)
        except subprocess.CalledProcessError as e:
            print(f"[UPLOAD] Failed ({e.returncode})")

    elif choice == "5":
        # ============================================================
        # FORCE-REGENERATE after normal render path
        # ============================================================
        print("[REGEN] Forcing MP4 regeneration…")
        new_cmd = [
            sys.executable,
            str(Path(__file__)),
            "--slug", slug,
            "--profile", profile,
            "--offset", str(LYRICS_OFFSET_SECS),
            "--force",
        ]
        print("[REGEN CMD]", " ".join(new_cmd))
        subprocess.run(new_cmd, check=True)

    else:
        print("[OPEN] No action.")


if __name__ == "__main__":
    main()

# end of 4_mp4.py
