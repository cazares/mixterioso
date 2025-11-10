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

# Layout: top band for main lyric, bottom band for "up next".
TOP_BAND_FRACTION = 0.8
BOTTOM_BAND_FRACTION = 0.2

# Nudge main line within the top band (fraction of top-band height; + = down).
VERTICAL_OFFSET_FRACTION = 0.0

# Extra nudge for the title line relative to the main line (fraction of top band).
TITLE_EXTRA_OFFSET_FRACTION = -0.20

# Fraction from top of bottom band to "up next" line.
BOTTOM_TEXT_TOP_PADDING_FRACTION = 0.20

# Size of up-next font relative to main lyrics.
NEXT_LINE_FONT_SCALE = 0.60

# Alpha for up-next text (00 = opaque, FF = fully transparent). Keep it partially transparent.
NEXT_LINE_ALPHA_HEX = "8080"

# Base UI font size in "points" (converted to ASS by a multiplier).
DEFAULT_UI_FONT_SIZE = 120
ASS_FONT_MULTIPLIER = 1.5  # multiple of UI font size to get ASS fontsize


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

    # Geometry
    top_band_height = int(playresy * TOP_BAND_FRACTION)
    y_divider = top_band_height
    bottom_band_height = playresy - y_divider

    center_top = top_band_height // 2
    offset_px = int(top_band_height * VERTICAL_OFFSET_FRACTION)
    y_main = center_top + offset_px
    y_title = y_main + int(top_band_height * TITLE_EXTRA_OFFSET_FRACTION)

    x_center = playresx // 2
    y_center_full = playresy // 2
    y_next = y_divider + int(bottom_band_height * BOTTOM_TEXT_TOP_PADDING_FRACTION)

    preview_font = max(1, int(font_size_script * NEXT_LINE_FONT_SCALE))
    margin_v = 0

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
        text = text.replace("{", "(").replace("}", ")")
        text = text.replace("\\N", "\\N")
        text = text.replace("\n", r"\N")
        return text

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

    offsettt = -10.0
    first_lyric_time = max(0.0, unified[0][0] + offsettt)
    

    # Intro title / artist card, centered, with no lyrics / previews / divider.
    title_lines = []
    if title:
        title_lines.append(title)
    if artist:
        title_lines.append(f"by {artist}")

    if title_lines:
        # End the intro at or before the first lyric so they never overlap.
        if first_lyric_time > 0.1:
            title_end = min(first_lyric_time, 5.0)
        else:
            title_end = first_lyric_time  # very short intro if lyrics start immediately

        intro_block = "\\N".join(title_lines)
        events.append(
            "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(0.0),
                end=seconds_to_ass_time(title_end),
                text=f"{{\\an5\\pos({x_center},{y_center_full})}}{ass_escape(intro_block)}",
            )
        )

    # Divider line across the screen at y_divider, only while lyrics are active.
    divider_text = (
        f"{{\\an7\\pos(0,{y_divider})\\p1\\1a&H{NEXT_LINE_ALPHA_HEX}&\\bord1}}"
        f"m 0 0 l {playresx} 0{{\\p0}}"
    )
    events.append(
        "Dialogue: 0,{start},{end},Default,,0,0,0,,{text}".format(
            start=seconds_to_ass_time(first_lyric_time),
            end=seconds_to_ass_time(audio_duration),
            text=divider_text,
        )
    )

    # One main line per event, one up-next line, no overlaps.
    n = len(unified)
    for i, (t, raw_text, _line_index) in enumerate(unified):
        start = max(0.0, t + offsettt)
        if i < n - 1:
            end = max(start, unified[i + 1][0] - offsettt)
        else:
            end = audio_duration or (start + 5.0)

        if end > audio_duration:
            end = audio_duration
        if end <= start:
            continue

        # Main line (lyric or note) in the top band.
        main_text = ass_escape(raw_text)
        events.append(
            "Dialogue: 1,{start},{end},Default,,0,0,0,,{text}".format(
                start=seconds_to_ass_time(start),
                end=seconds_to_ass_time(end),
                text=f"{{\\an5\\pos({x_center},{y_main})}}{main_text}",
            )
        )

        # Up-next preview: next event's text, bottom band, semi-transparent.
        if i < n - 1:
            _, next_raw, _ = unified[i + 1]
            if next_raw:
                preview_text = ass_escape(next_raw)
                tag = (
                    f"{{\\an5\\pos({x_center},{y_next})"
                    f"\\fs{preview_font}\\1a&H{NEXT_LINE_ALPHA_HEX}&}}"
                )
                events.append(
                    "Dialogue: 2,{start},{end},Default,,0,0,0,,{text}".format(
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
