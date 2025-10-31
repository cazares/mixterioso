#!/usr/bin/env python3
# render_from_csv.py
# CSV (line,start[,end]) -> ASS -> ffmpeg -> MP4
# intro-hold is now clamped to the *raw* time of the 2nd lyric (before offset-video),
# so a huge --intro-hold (e.g. 100s) won't make offset-video *look* ignored.

import argparse
import csv
import os
import random
import subprocess
import tempfile
from typing import List, Dict, Any, Optional


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Render karaoke MP4 from CSV timings + audio using ffmpeg + libass."
    )
    p.add_argument("--csv", required=True, help="CSV with columns: line,start[,end,...]")
    p.add_argument("--audio", required=True, help="Audio file (mp3/wav) to mux")
    p.add_argument("--repo-root", required=True, help="Root of karaoke repo (for output/)")
    p.add_argument("--font-size", type=int, default=60, help="Main font size")
    p.add_argument("--car-font-size", type=int, default=None, help="Optional smaller font for gap line")
    p.add_argument("--font-name", default="ArialMT", help="ASS font name to use")
    p.add_argument("--offset-video", type=float, default=0.0, help="Shift subs vs audio (negative = delay)")
    p.add_argument("--extra-delay", type=float, default=0.0, help="Extra delay added to every line start")
    p.add_argument("--hpad-pct", type=float, default=6.0, help="Horizontal padding (percent per side)")
    p.add_argument("--valign", default="middle", choices=["top", "middle", "bottom"], help="Vertical alignment")
    p.add_argument("--output-name", default="output", help="Base name (no extension) for output mp4")
    p.add_argument("--max-chars", type=int, default=0, help="Hard wrap at this many chars (0 = no wrap)")
    p.add_argument("--artist", default="", help="For intro screen")
    p.add_argument("--title", default="", help="For intro screen")
    p.add_argument("--gap-threshold", type=float, default=5.0, help="Seconds of silence to trigger filler")
    p.add_argument("--gap-delay", type=float, default=2.0, help="Seconds AFTER line end before filler shows")
    p.add_argument(
        "--intro-hold",
        type=float,
        default=5.0,
        help="Preferred intro duration (will be clamped to 2nd lyric raw time)",
    )
    p.add_argument("--no-open", action="store_true", help="Do not open output dir on macOS")
    return p.parse_args()


def read_csv_rows(path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if "line" not in row or "start" not in row:
                continue
            text = row["line"].strip()
            try:
                start = float(row["start"])
            except ValueError:
                continue
            end_raw = row.get("end", "").strip()
            end_val = float(end_raw) if end_raw else None
            rows.append({"line": text, "start": start, "end": end_val})
    rows.sort(key=lambda x: x["start"])
    return rows


def wrap_line(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return text
    words = text.split()
    lines: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for w in words:
        add_len = len(w) + (1 if cur else 0)
        if cur_len + add_len > max_chars:
            lines.append(" ".join(cur))
            cur = [w]
            cur_len = len(w)
        else:
            cur.append(w)
            cur_len += add_len
    if cur:
        lines.append(" ".join(cur))
    return "\\N".join(lines)


def make_ass_style(font_name: str,
                   font_size: int,
                   hpad_pct: float,
                   valign: str,
                   car_font_size: int = None) -> str:
    margin_lr = int(1280 * (hpad_pct / 100.0))

    if valign == "top":
        alignment = 8
        margin_v = 30
    elif valign == "bottom":
        alignment = 2
        margin_v = 30
    else:
        alignment = 5
        margin_v = 0

    size_main = font_size
    size_gap = car_font_size if car_font_size is not None else font_size

    return (
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},{size_main},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,3,0,"
        f"{alignment},{margin_lr},{margin_lr},{margin_v},0\n"
        f"Style: Gap,{font_name},{size_gap},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,3,0,"
        f"{alignment},{margin_lr},{margin_lr},{margin_v},0\n"
    )


def make_ass_header(font_name: str,
                    font_size: int,
                    hpad_pct: float,
                    valign: str,
                    car_font_size: int = None) -> str:
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1280\n"
        "PlayResY: 720\n"
        "YCbCr Matrix: TV.601\n"
        "\n"
        + make_ass_style(font_name, font_size, hpad_pct, valign, car_font_size)
        + "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def sec_to_ass_time(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60.0
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_intro_events(artist: str, title: str, intro_end: float) -> List[str]:
    if not artist and not title:
        return []
    if artist and title:
        text = f"{title}\\Nby\\N{artist}"
    elif title:
        text = title
    else:
        text = artist
    return [f"Dialogue: 0,{sec_to_ass_time(0.0)},{sec_to_ass_time(intro_end)},Default,,0,0,0,,{text}"]


def build_gap_event(start: float, end: float) -> str:
    symbols = ["♬", "♫", "♪", "♩"]
    random.shuffle(symbols)
    text = "".join(symbols)
    return f"Dialogue: 0,{sec_to_ass_time(start)},{sec_to_ass_time(end)},Gap,,0,0,0,,{text}"


def build_ass_events(rows: List[Dict[str, Any]],
                     extra_delay: float,
                     offset_video: float,
                     gap_threshold: float,
                     gap_delay: float,
                     artist: str,
                     title: str,
                     intro_end: float) -> List[str]:
    events: List[str] = []
    events.extend(build_intro_events(artist, title, intro_end))

    for i, row in enumerate(rows):
        start = row["start"] + extra_delay - offset_video
        end = row["end"]
        if end is None:
            if i + 1 < len(rows):
                end = rows[i + 1]["start"] + extra_delay - offset_video
            else:
                end = start + 2.0
        else:
            end = end + extra_delay - offset_video

        text = row["line"]
        events.append(
            f"Dialogue: 0,{sec_to_ass_time(start)},{sec_to_ass_time(end)},Default,,0,0,0,,{text}"
        )

        if i + 1 < len(rows):
            next_start = rows[i + 1]["start"] + extra_delay - offset_video
            gap = next_start - end
            if gap >= gap_threshold:
                gap_start = end + gap_delay
                if gap_start < next_start and gap_start >= intro_end:
                    events.append(build_gap_event(gap_start, next_start))

    return events


def write_ass(tmp_ass: str,
              rows: List[Dict[str, Any]],
              args: argparse.Namespace,
              intro_end: float) -> None:
    header = make_ass_header(
        args.font_name,
        args.font_size,
        args.hpad_pct,
        args.valign,
        args.car_font_size,
    )
    events = build_ass_events(
        rows,
        args.extra_delay,
        args.offset_video,
        args.gap_threshold,
        args.gap_delay,
        args.artist,
        args.title,
        intro_end,
    )
    with open(tmp_ass, "w", encoding="utf-8") as f:
        f.write(header)
        for e in events:
            f.write(e + "\n")


def get_audio_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            encoding="utf-8",
        )
        return float(out.strip())
    except Exception:
        return 0.0


def run_ffmpeg(ass_path: str, audio_path: str, duration: float, out_path: str):
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s=1280x720:d={duration:.2f}",
        "-i", audio_path,
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_path,
    ]
    subprocess.check_call(cmd)


def main():
    args = parse_args()
    rows = read_csv_rows(args.csv)
    if not rows:
        raise SystemExit("CSV has no usable rows")

    # hard wrap first
    if args.max_chars and args.max_chars > 0:
        for r in rows:
            r["line"] = wrap_line(r["line"], args.max_chars)

    # raw (NO offset-video) lyric times
    first_raw = rows[0]["start"] + args.extra_delay
    second_raw: Optional[float] = None
    if len(rows) > 1:
        second_raw = rows[1]["start"] + args.extra_delay

    # clamp intro-hold to second_raw (if exists), never below 3s, and never before first_raw
    intro_end = max(3.0, args.intro_hold)
    if second_raw is not None and intro_end > second_raw:
        intro_end = second_raw
    if intro_end < first_raw:
        intro_end = first_raw

    out_dir = os.path.join(args.repo_root, "output")
    os.makedirs(out_dir, exist_ok=True)
    out_mp4 = os.path.join(out_dir, f"{args.output_name}.mp4")

    with tempfile.TemporaryDirectory() as tmpd:
        ass_path = os.path.join(tmpd, "lyrics.ass")
        write_ass(ass_path, rows, args, intro_end)

        audio_dur = get_audio_duration(args.audio)
        csv_last_end = max((r["end"] if r["end"] is not None else r["start"] + 2.0) for r in rows)
        csv_last_end = csv_last_end + args.extra_delay - args.offset_video

        duration = max(audio_dur, csv_last_end + 0.25, intro_end + 0.25)

        try:
            run_ffmpeg(ass_path, args.audio, duration, out_mp4)
            print(f"[OK] wrote {out_mp4}")
        except subprocess.CalledProcessError as e:
            print("ffmpeg failed:", e)

    if not args.no_open and os.name == "posix":
        try:
            subprocess.Popen(["open", out_dir])
        except Exception:
            pass


if __name__ == "__main__":
    main()
# end of render_from_csv.py
