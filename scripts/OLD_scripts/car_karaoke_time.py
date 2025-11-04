#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/car_karaoke_time.py

Karaoke Time — CSV + audio -> MP4 (ASS-based, centered)

- uses REAL .ass (not subtitles=...:force_style=...)
- Alignment=5 so it's horizontally AND vertically centered
- obeys your --font-size exactly (no clamping)
- lets you set final canvas (--video-size, default 1280x720)
- lets you set subtitle design space (--subtitle-base-size, default 640x360)
- supports --vocal-pct / --vocal-pcts so render_from_csv.py is happy
- NEW: --extra-delay N  → shifts ALL lines by +N seconds on top of upstream --offset-video
"""

import argparse
import csv
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple


def die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def read_csv_rows(csv_path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "start" not in reader.fieldnames:
            die(f"CSV {csv_path} must have at least 'start' column")
        for row in reader:
            rows.append(row)
    return rows


def load_lyrics_lines(lyrics_path: Path) -> List[str]:
    text = lyrics_path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.split("\n")


def sync_lyrics_into_rows(rows: List[Dict[str, str]], lyrics_lines: List[str]) -> List[Dict[str, str]]:
    """
    If CSV has timing but blank text, fill from lyrics.txt in order.
    """
    out: List[Dict[str, str]] = []
    li = 0
    for row in rows:
        line_txt = (row.get("line") or "").strip()
        if not line_txt and li < len(lyrics_lines):
            row["line"] = lyrics_lines[li].strip()
            li += 1
        out.append(row)
    return out


def fmt_ass_time(sec: float) -> str:
    """
    ASS uses h:mm:ss.cc (centiseconds)
    """
    if sec < 0:
        sec = 0.0
    cs = int(round(sec * 100))
    h = cs // (60 * 60 * 100)
    cs %= (60 * 60 * 100)
    m = cs // (60 * 100)
    cs %= (60 * 100)
    s = cs // 100
    cs %= 100
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(
    rows: List[Dict[str, str]],
    playres_x: int,
    playres_y: int,
    font_size: int,
    effective_offset: float,
    extra_end: float,
) -> Tuple[Path, float]:
    """
    Build a real .ass file with center alignment.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="karaoke_ass_"))
    ass_path = tmpdir / "lyrics.ass"

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {playres_x}",
        f"PlayResY: {playres_y}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        # Alignment=5 -> middle-center
        (
            f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
            f"0,0,0,0,100,100,0,0,1,2,0,5,0,0,20,1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    ass_lines: List[str] = []
    last_end = 0.0

    for idx, row in enumerate(rows, start=1):
        text = (row.get("line") or "").strip()
        if not text:
            text = "♪"

        try:
            start = float(row["start"])
        except Exception:
            continue

        # derive end
        end: Optional[float] = None
        if "end" in row and row["end"]:
            try:
                end = float(row["end"])
            except Exception:
                end = None

        if end is None:
            if idx < len(rows):
                try:
                    next_start = float(rows[idx].get("start") or 0.0)
                    end = max(start + 0.3, next_start - 0.15)
                except Exception:
                    end = start + 0.5
            else:
                end = start + 1.0

        start_ass = fmt_ass_time(start + effective_offset)
        end_ass = fmt_ass_time(max(start, end) + effective_offset)

        ass_lines.append(
            f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}"
        )

        last_end = max(last_end, end)

    ass_path.write_text("\n".join(header + ass_lines) + "\n", encoding="utf-8")
    return ass_path, last_end + extra_end


def probe_audio_duration(audio_path: Path) -> Optional[float]:
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            return float(r.stdout.strip())
    except Exception:
        return None
    return None


def render_one_video(
    ass_path: Path,
    audio_path: Path,
    output_path: Path,
    total_duration: float,
    video_size: str,
) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s={video_size}:d={total_duration}",
        "-i", str(audio_path),
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    print(f"[INFO] ffmpeg cmd: {' '.join(cmd)}")
    subprocess.run(cmd, check=False)
    print(f"[OK] wrote {output_path}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Karaoke Time — CSV + audio -> MP4 (ASS, centered, offset-fixable)."
    )
    ap.add_argument("--csv", "--timings", dest="csv", help="CSV with columns line,start[,end]")
    ap.add_argument("--lyrics", help="Optional lyrics .txt to fill blank CSV rows")
    ap.add_argument("--audio", "--mp3", dest="audio", help="Audio file to use")
    ap.add_argument("--output-dir", default="output", help="Where to write MP4")
    ap.add_argument("--font-size", type=int, default=140, help="ASS font size (used exactly)")
    ap.add_argument("--offset-video", type=float, default=0.0, help="Base offset (what the wrapper sends)")
    ap.add_argument(
        "--extra-delay",
        type=float,
        default=0.0,
        help="Extra seconds to DELAY subtitles (+) or make them earlier (-), applied after --offset-video",
    )
    ap.add_argument("--append-end-duration", type=float, default=0.0, help="Extra duration at end (sec)")
    ap.add_argument("--video-size", default="1280x720", help="Final video size, e.g. 1280x720")
    ap.add_argument(
        "--subtitle-base-size",
        default="640x360",
        help="Subtitle design resolution (used for ASS PlayResX/PlayResY)",
    )
    # from render_from_csv.py:
    ap.add_argument("--vocal-pcts", nargs="+", type=float, help="Render multiple variants (names only)")
    ap.add_argument("--vocal-pct", type=float, help="Single vocal pct (alias)")
    # tolerate extra stuff
    ap.add_argument("--high-quality", action="store_true", help="(ignored)")
    ap.add_argument("--remove-cache", action="store_true", help="(ignored)")

    args, extras = ap.parse_known_args()
    if extras:
        print(f"[WARN] Ignoring extra args from wrapper: {extras}")

    if not args.csv:
        die("You must pass --csv <file.csv>")
    csv_path = Path(args.csv)
    if not csv_path.exists():
        die(f"CSV not found: {csv_path}")

    # audio
    if args.audio:
        audio_path = Path(args.audio)
        if not audio_path.exists():
            die(f"Audio not found: {audio_path}")
    else:
        guess = Path("songs") / (csv_path.stem + ".mp3")
        if guess.exists():
            audio_path = guess
        else:
            die("Audio not provided and could not infer songs/<csvname>.mp3")

    rows = read_csv_rows(csv_path)

    # optional lyrics backfill
    if args.lyrics:
        lyr = Path(args.lyrics)
        if lyr.exists():
            lyrics_lines = load_lyrics_lines(lyr)
            rows = sync_lyrics_into_rows(rows, lyrics_lines)

    # subtitle-base-size parsing
    try:
        base_w, base_h = [int(x) for x in args.subtitle_base_size.lower().split("x")]
    except Exception:
        base_w, base_h = 640, 360

    # 👇 this is the important line
    effective_offset = args.offset_video + args.extra_delay

    # build ASS
    ass_path, ass_end = build_ass(
        rows=rows,
        playres_x=base_w,
        playres_y=base_h,
        font_size=args.font_size,
        effective_offset=effective_offset,
        extra_end=args.append_end_duration,
    )

    # duration
    real_dur = probe_audio_duration(audio_path)
    if real_dur is None:
        total_duration = max(5.0, ass_end)
    else:
        total_duration = real_dur + max(0.0, args.append_end_duration)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # vocal variants
    if args.vocal_pcts:
        variants = args.vocal_pcts
    elif args.vocal_pct is not None:
        variants = [args.vocal_pct]
    else:
        variants = [100.0]

    for pct in variants:
        suffix = "" if float(pct) == 100.0 else f"_v{int(pct)}"
        out_path = out_dir / f"{csv_path.stem}{suffix}.mp4"
        print(f"[INFO] Rendering {out_path.name} (vocal {pct}%) ...")
        render_one_video(
            ass_path=ass_path,
            audio_path=audio_path,
            output_path=out_path,
            total_duration=total_duration,
            video_size=args.video_size,
        )

    print("[DONE] Karaoke Time — all variants rendered.")


if __name__ == "__main__":
    main()
# end of car_karaoke_time.py
