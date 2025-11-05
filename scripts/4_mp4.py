#!/usr/bin/env python3
import argparse
import csv
import json
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
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"

VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080

# How far above vertical center to place text, as a fraction of screen height.
# Positive = move text up.
VERTICAL_OFFSET_FRACTION = 0.12  # tweak this easily in code

# ASS "Fontsize" is relative to PlayResY, not literal pixels.
# This multiplier makes UI font sizes (20–200) visually larger on 1080p.
ASS_FONT_MULTIPLIER = 1.5

# Next-line preview tuning (relative to screen / main font).
NEXT_LINE_X_OFFSET_FRACTION = 0.15   # shift right from center
NEXT_LINE_Y_OFFSET_FRACTION = 0.10   # shift down from main line
NEXT_LINE_FONT_SCALE = 0.5           # 50% of main font size
NEXT_LINE_ALPHA_HEX = "80"           # &H00..& = opaque, &HFF..& = transparent


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def probe_audio_duration(path: Path) -> float:
    """Return audio duration in seconds using ffprobe."""
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ]
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out)
    except Exception as e:
        log("FFPROBE", f"Failed to probe duration for {path}: {e}", YELLOW)
        return 0.0


def seconds_to_ass_time(sec: float) -> str:
    # ASS time format: H:MM:SS.cs (centiseconds)
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    rem = sec - h * 3600
    m = int(rem // 60)
    s = rem - m * 60
    cs = int(round(s * 100))  # centiseconds
    if cs == 100:
        s = int(s) + 1
        cs = 0
    s = int(sec) % 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


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


def read_timings(slug: str) -> list[tuple[float, str]]:
    """
    Read timings CSV for slug and return a list of (time_secs, text).

    Supports the format written by 3_timing.py:
        line_index,time_secs,text
    and falls back to a 2-column (time,text) format if present.
    """
    timing_path = TIMINGS_DIR / f"{slug}.csv"
    if not timing_path.exists():
        raise SystemExit(f"Timings CSV not found: {timing_path}")

    rows: list[tuple[float, str]] = []
    with timing_path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        if header and "time_secs" in header:
            # Preferred format: line_index,time_secs,text
            try:
                idx_time = header.index("time_secs")
            except ValueError:
                idx_time = 1
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
                text = ""
                if idx_text is not None and len(row) > idx_text:
                    text = row[idx_text]
                rows.append((t, text))
        else:
            # Fallback: treat first column as time, second as text
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
                rows.append((t, text))

    rows.sort(key=lambda x: x[0])
    log("TIMINGS", f"Loaded {len(rows)} timing rows from {timing_path}", CYAN)
    return rows


def build_ass(
    slug: str,
    artist: str,
    title: str,
    timings: list[tuple[float, str]],
    audio_duration: float,
    font_name: str,
    font_size_script: int,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUTPUT_DIR / f"{slug}.ass"

    if audio_duration <= 0.0 and timings:
        audio_duration = max(t for t, _ in timings) + 5.0

    # Playback resolution
    playresx = VIDEO_WIDTH
    playresy = VIDEO_HEIGHT

    # Vertical offset for main line.
    margin_v = int(playresy * VERTICAL_OFFSET_FRACTION)

    # Approximate main line anchor and next-line offset positions.
    x_center = int(playresx / 2)
    y_main = int(playresy / 2 - margin_v)
    x_preview = int(x_center + playresx * NEXT_LINE_X_OFFSET_FRACTION)
    y_preview = int(y_main + playresy * NEXT_LINE_Y_OFFSET_FRACTION)
    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))

    # Basic ASS header
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
            "&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,4,0,5,50,50,"
            f"{margin_v},0"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ass_escape(text: str) -> str:
        # Basic escaping for ASS: replace { } and newlines
        text = text.replace("{", "(").replace("}", ")")
        text = text.replace("\\N", "\\N")  # keep explicit \N
        text = text.replace("\n", r"\N")
        return text

    events: list[str] = []
    if not timings:
        # Single event with placeholder
        start = 0.0
        end = audio_duration or 5.0
        ev_text = f"{title}\\Nby\\N{artist}" if artist else title
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=ass_escape(ev_text),
            )
        )
    else:
        # Build events from timings; each entry lasts until next timestamp,
        # last entry lasts until audio_duration.
        n = len(timings)
        for i, (t, text) in enumerate(timings):
            start = t
            if i < n - 1:
                end = timings[i + 1][0]
            else:
                end = audio_duration or (t + 5.0)
            if end <= start:
                end = start + 0.5

            # Main line (centered, full size)
            main_text = ass_escape(text)
            events.append(
                "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                    start=seconds_to_ass_time(start),
                    end=seconds_to_ass_time(end),
                    text=main_text,
                )
            )

            # Next-line preview (below/right, smaller, semi-transparent)
            if i < n - 1:
                next_raw = timings[i + 1][1]
                if next_raw:
                    preview_text = ass_escape(next_raw)
                    tag = "{{\\pos({x},{y})\\fs{fs}\\1a&H{alpha}&}}".format(
                        x=x_preview,
                        y=y_preview,
                        fs=preview_font,
                        alpha=NEXT_LINE_ALPHA_HEX,
                    )
                    events.append(
                        "Dialogue: 1,{start},{end},Default,,0,0,0,,{text}".format(
                            start=seconds_to_ass_time(start),
                            end=seconds_to_ass_time(end),
                            text=tag + preview_text,
                        )
                    )

    ass_path.write_text("\n".join(header_lines + events) + "\n", encoding="utf-8")
    log("ASS", f"Wrote ASS subtitles to {ass_path}", GREEN)
    return ass_path


def choose_audio(slug: str, profile: str) -> Path:
    mix_wav = MIXES_DIR / f"{slug}_{profile}.wav"
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

    if mp3_path.exists():
        print(
            f"[AUDIO] Mixed WAV for profile={profile} not found.\n"
            f"        Falling back to original mp3: {mp3_path}"
        )
        return mp3_path

    print(
        f"Audio not found for slug={slug}, profile={profile}.\n"
        f"Tried:\n"
        f"  mix: {mix_wav}\n"
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
        help="Mix profile name (matches WAV name in mixes/).",
    )
    p.add_argument(
        "--font-size",
        type=int,
        help="Subtitle font size (20–200). Default 140.",
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

    # Determine font size, with interactive prompt when possible.
    default_font_size = 140
    font_size_value = args.font_size

    if font_size_value is None:
        if sys.stdin.isatty():
            try:
                resp = input(
                    f"Subtitle font size [20–200, default {default_font_size}]: "
                ).strip()
            except EOFError:
                resp = ""
            if resp:
                try:
                    font_size_value = int(resp)
                except ValueError:
                    log("FONT", f"Invalid font size '{resp}', using default {default_font_size}", YELLOW)
                    font_size_value = default_font_size
            else:
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

    # Choose audio path (with fallback logic)
    audio_path = choose_audio(slug, profile)
    audio_duration = probe_audio_duration(audio_path)
    if audio_duration <= 0:
        log("DUR", f"Audio duration unknown or zero for {audio_path}", YELLOW)

    # Load meta + timings
    artist, title = read_meta(slug)
    timings = read_timings(slug)
    log("META", f'Artist="{artist}", Title="{title}", entries={len(timings)}', CYAN)

    # Build ASS (use scaled ASS font size)
    ass_path = build_ass(slug, artist, title, timings, audio_duration, args.font_name, ass_font_size)

    # Output MP4 path
    out_mp4 = OUTPUT_DIR / f"{slug}_{profile}.mp4"

    # ffmpeg pipeline: audio input + black background + ASS subtitles
    # Input 0: audio, Input 1: black video
    color_filter = f"color=c=black:s={VIDEO_WIDTH}x{VIDEO_HEIGHT}:r=30:d={max(audio_duration, 5.0)}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(audio_path),
        "-f",
        "lavfi",
        "-i",
        color_filter,
        "-vf",
        f"subtitles={ass_path}",
        "-map",
        "1:v",
        "-map",
        "0:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
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

    # Offer to open dir/mp4
    print()
    print(f"{BOLD}{BLUE}MP4 generation complete:{RESET} {out_mp4}")
    print("What would you like to open?")
    print("  1 = output directory")
    print("  2 = MP4 file")
    print("  3 = both (dir then MP4)")
    print("  0 = none")
    choice = ""
    try:
        choice = input("Choice [0/1/2/3, default 0]: ").strip()
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
