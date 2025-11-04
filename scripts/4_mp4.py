#!/usr/bin/env python3
import argparse
import csv
import json
import platform
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


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def fmt_secs_mmss(sec: float) -> str:
    m = int(sec // 60)
    s = int(round(sec - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{sec:6.2f} s  ({m:02d}:{s:02d})"


def ask_font_size(default: int = 120) -> int:
    prompt = f"Global font size default {default}. ENTER to accept or type 20–200: "
    try:
        s = input(prompt).strip()
    except EOFError:
        return default
    if not s:
        return default
    try:
        v = int(s)
        if 20 <= v <= 200:
            return v
    except ValueError:
        pass
    log("MP4", f"Invalid font size; using {default}.", YELLOW)
    return default


def secs_to_ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    rem = t - h * 3600
    m = int(rem // 60)
    s = rem - m * 60
    sec = int(s)
    cs = int(round((s - sec) * 100))
    if cs == 100:
        sec += 1
        cs = 0
    if sec == 60:
        m += 1
        sec = 0
    return f"{h:d}:{m:02d}:{sec:02d}.{cs:02d}"


def escape_ass_text(s: str) -> str:
    return s.replace("\n", r"\N").replace("{", r"\{").replace("}", r"\}")


def load_meta(slug: str):
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data.get("artist"), data.get("title")
    except Exception:
        return None, None


def load_timings(slug: str):
    path = TIMINGS_DIR / f"{slug}.csv"
    if not path.exists():
        raise SystemExit(f"Timing CSV not found: {path}")
    rows = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            idx = int(row.get("line_index", "-1"))
            text = row.get("text", "")
            rows.append({"time": t, "line_index": idx, "text": text})
    rows.sort(key=lambda r: r["time"])
    if not rows:
        raise SystemExit(f"No usable rows in timings CSV: {path}")
    return rows, path


def build_ass_from_timings(slug: str, rows, duration: float, lyric_font_size: int) -> Path:
    META_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = META_DIR / f"{slug}_lyrics.ass"

    # Alignment=5 => centered vertically and horizontally (like title card)
    header = f"""[Script Info]
ScriptType: v4.00+
Collisions: Normal
PlayResX: 1920
PlayResY: 1080
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{lyric_font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,5,80,80,40,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]

    n = len(rows)
    for i, row in enumerate(rows):
        start = row["time"]
        if i + 1 < n:
            end = rows[i + 1]["time"]
        else:
            end = min(duration, start + 3.0)
        if end <= start:
            end = start + 0.5

        text = escape_ass_text(row.get("text", ""))

        start_str = secs_to_ass_time(start)
        end_str = secs_to_ass_time(end)
        line = f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n"
        lines.append(line)

    ass_path.write_text("".join(lines), encoding="utf-8")
    log("ASS", f"Wrote ASS subtitles to {ass_path}", GREEN)
    return ass_path


def ffprobe_duration(audio_path: Path) -> float:
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
    out = subprocess.check_output(cmd, text=True).strip()
    try:
        return float(out)
    except ValueError:
        raise SystemExit(f"Could not parse duration from ffprobe output: {out!r}")


def open_path(path: Path) -> None:
    system = platform.system().lower()
    try:
        if system == "darwin":
            subprocess.run(["open", str(path)], check=False)
        elif system == "windows":
            subprocess.run(["explorer", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        log("OPEN", f"Failed to open {path}", YELLOW)


def escape_drawtext_text(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate MP4 with title card and lyrics/notes from timings.")
    p.add_argument("--slug", type=str, required=True, help="Song slug (e.g. californication)")
    p.add_argument(
        "--profile",
        type=str,
        default="karaoke",
        choices=["lyrics", "karaoke", "car-karaoke", "no-bass", "car-bass-karaoke"],
        help="Audio profile (lyrics=original mp3, others=use mixed WAV)",
    )
    p.add_argument("--font-size", type=int, help="Global font size 20–200 (title + lyrics).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MIXES_DIR.mkdir(parents=True, exist_ok=True)

    if args.profile == "lyrics":
        audio_path = MP3_DIR / f"{slug}.mp3"
    else:
        audio_path = MIXES_DIR / f"{slug}_{args.profile}.wav"

    if not audio_path.exists():
        raise SystemExit(f"Audio not found for profile={args.profile}: {audio_path}")

    duration = ffprobe_duration(audio_path)
    rows, timing_csv_path = load_timings(slug)
    log("MP4", f"Using timings from {timing_csv_path}", GREEN)

    if args.font_size is not None:
        font_size = args.font_size
    else:
        font_size = ask_font_size(120)

    # lyrics use the same size now so the change is obvious
    lyric_font_size = font_size
    ass_path = build_ass_from_timings(slug, rows, duration, lyric_font_size)

    artist, title = load_meta(slug)
    if title and artist:
        title_main = title
        title_by = "by"
        title_artist = artist
    elif title:
        title_main = title
        title_by = ""
        title_artist = ""
    else:
        title_main = slug.replace("_", " ")
        title_by = ""
        title_artist = ""

    META_DIR.mkdir(parents=True, exist_ok=True)
    out_mp4 = OUTPUT_DIR / f"{slug}_{args.profile}.mp4"

    ass_str = ass_path.as_posix()

    # Title: same drawtext style as before, three lines if artist present
    draw_layers = []
    in_label = "sub"

    if title_main and title_artist:
        y1 = f"h/2-{font_size*1.3:.1f}"
        y2 = "h/2"
        y3 = f"h/2+{font_size*1.3:.1f}"
        triples = [
            (title_main, y1),
            (title_by, y2),
            (title_artist, y3),
        ]
    elif title_main and title_by:
        y1 = f"h/2-{font_size*0.8:.1f}"
        y2 = f"h/2+{font_size*0.8:.1f}"
        triples = [
            (title_main, y1),
            (title_by, y2),
        ]
    else:
        triples = [(title_main, "(h-text_h)/2")]

    for i, (text, yexpr) in enumerate(triples):
        if not text:
            continue
        out_label = "v" if i == len(triples) - 1 else f"t{i+1}"
        draw = (
            f"[{in_label}]drawtext=text='{escape_drawtext_text(text)}':"
            f"fontcolor=white:fontsize={font_size}:"
            f"x=(w-text_w)/2:y={yexpr}:enable='lte(t,3)'[{out_label}]"
        )
        draw_layers.append(draw)
        in_label = out_label

    if not draw_layers:
        filter_complex = f"[0:v]subtitles={ass_str}[v]"
    else:
        draw_chain = ";".join(draw_layers)
        filter_complex = f"[0:v]subtitles={ass_str}[sub];{draw_chain}"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=size=1920x1080:rate=30:color=black",
        "-i",
        str(audio_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(out_mp4),
    ]

    log("FFMPEG", " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    t1 = time.perf_counter()

    log("MP4", f"MP4 written to {out_mp4}", GREEN)
    log("MP4", f"Render time: {fmt_secs_mmss(t1 - t0)}", GREEN)
    if title_main:
        title_desc = " / ".join([p for p in [title_main, title_by, title_artist] if p])
        log("MP4", f'Title card "{title_desc}" shown in first ~3s.', GREEN)
    log("MP4", f"Subtitles from {timing_csv_path}", GREEN)
    log("MP4", f"Global font size {font_size} (lyrics {lyric_font_size}).", GREEN)

    print()
    print("What next?")
    print("  1  open output directory")
    print("  2  open MP4")
    print("  3  open both (dir first, then MP4)")
    print("  0  nothing")
    try:
        choice = input("Choice [0-3, ENTER=0]: ").strip()
    except EOFError:
        choice = "0"

    if choice == "1":
        open_path(out_mp4.parent)
    elif choice == "2":
        open_path(out_mp4)
    elif choice == "3":
        open_path(out_mp4.parent)
        open_path(out_mp4)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
