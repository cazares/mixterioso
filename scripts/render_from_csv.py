#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_from_csv.py — burn timed lyrics (from CSV) onto a 1280x720 video using ASS,
with font-size–aware wrapping that’s a bit LOOSER (wider lines) so 140pt looks normal.
"""

import argparse
import csv
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import textwrap


def ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    cs = int(round((t - int(t)) * 100))  # centiseconds
    s = int(t) % 60
    m = (int(t) // 60) % 60
    h = int(t) // 3600
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def read_csv_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            line = (row.get("line") or "").strip()
            try:
                start = float(row.get("start", "0") or 0)
            except ValueError:
                start = 0.0
            try:
                end = float(row.get("end", "0") or 0)
            except ValueError:
                end = start
            rows.append((line, start, end))
    return rows


def ass_escape(s: str) -> str:
    # protect ASS overrides
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def wrap_text_for_ass(text: str, max_chars: int) -> str:
    """
    Turn a long line into multiple lines using ASS \\N.
    """
    if max_chars <= 0:
        return ass_escape(text)
    parts = textwrap.wrap(text, width=max_chars)
    return "\\N".join(ass_escape(p) for p in parts)


def ffprobe_duration_secs(audio_path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            text=True,
        ).strip()
        return float(out)
    except Exception:
        return 0.0


def write_ass(
    csv_rows,
    ass_path,
    *,
    width,
    height,
    font_name,
    font_size,
    margin_l,
    margin_r,
    margin_v,
    alignment_num,
    extra_delay,
    max_chars,
):
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\n")
        f.write("ScriptType: v4.00+\n")
        f.write("ScaledBorderAndShadow: yes\n")
        f.write("WrapStyle: 2\n")
        f.write(f"PlayResX: {width}\n")
        f.write(f"PlayResY: {height}\n\n")

        f.write("[V4+ Styles]\n")
        f.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        f.write(
            "Style: Karaoke,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            "0,0,0,0,100,100,0,0,1,0,0,{align},{ml},{mr},{mv},1\n".format(
                font=font_name,
                size=font_size,
                align=alignment_num,
                ml=margin_l,
                mr=margin_r,
                mv=margin_v,
            )
        )

        f.write("\n[Events]\n")
        f.write(
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        for line, start, end in csv_rows:
            st = max(0.0, start + extra_delay)
            et = max(st + 0.01, end + extra_delay)
            txt = wrap_text_for_ass(line, max_chars)
            f.write(
                f"Dialogue: 0,{ass_time(st)},{ass_time(et)},Karaoke,,0,0,0,,{txt}\n"
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--font-size", type=int, default=40)
    ap.add_argument("--repo-root", dest="repo_root", default=".")
    ap.add_argument("--offset-video", dest="offset_video", type=float, default=-1.0)
    ap.add_argument("--extra-delay", dest="extra_delay", type=float, default=0.0)
    ap.add_argument("--hpad-pct", dest="hpad_pct", type=float, default=6.0)
    ap.add_argument("--valign", choices=["top", "middle", "bottom"], default="middle")
    ap.add_argument("--vshift-px", type=int, default=0)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--font-name", default="Arial")
    ap.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="Force-wrap lines longer than this many chars using ASS \\N. 0 = auto from font & width.",
    )
    args = ap.parse_args()

    repo_root = os.path.abspath(args.repo_root)
    out_dir = os.path.join(repo_root, "output")
    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.abspath(args.csv)
    audio_path = os.path.abspath(args.audio)
    base = os.path.splitext(os.path.basename(csv_path))[0]
    mp4_out = os.path.join(out_dir, f"{base}.mp4")

    rows = read_csv_rows(csv_path)
    if not rows:
        print("[ERROR] CSV appears empty or missing header line,start,end", file=sys.stderr)
        sys.exit(2)

    # margins
    margin_px = max(0, int(round(args.width * (args.hpad_pct / 100.0))))
    margin_l = margin_px
    margin_r = margin_px
    margin_v = max(0, int(args.vshift_px))

    # alignment mapping
    if args.valign == "top":
        alignment_num = 8   # top-center
    elif args.valign == "bottom":
        alignment_num = 2   # bottom-center
    else:
        alignment_num = 5   # middle-center

    # ---------- font-size–aware auto wrap (LOOSER) ----------
    if args.max_chars > 0:
        max_chars = args.max_chars
    else:
        # usable width after margins
        usable_w = args.width * (1.0 - 2.0 * (args.hpad_pct / 100.0))
        # a bit more generous: characters are ~0.45 * font_size wide
        avg_char_px = max(args.font_size * 0.45, 1.0)
        est_chars = int(usable_w / avg_char_px)
        # don’t let it go too skinny, but don’t let it go forever either
        max_chars = max(12, min(est_chars, 60))

    with tempfile.TemporaryDirectory() as tmpd:
        ass_path = os.path.join(tmpd, "lyrics.ass")
        write_ass(
            rows,
            ass_path,
            width=args.width,
            height=args.height,
            font_name=args.font_name,
            font_size=args.font_size,
            margin_l=margin_l,
            margin_r=margin_r,
            margin_v=margin_v,
            alignment_num=alignment_num,
            extra_delay=args.extra_delay,
            max_chars=max_chars,
        )

        dur = ffprobe_duration_secs(audio_path)
        if dur <= 0:
            print("[WARN] Could not read duration from audio; defaulting to 180s.")
            dur = 180.0

        # ffmpeg ass filter
        ass_escaped = ass_path.replace("'", r"'\''")
        sub_filter = f"ass='{ass_escaped}'"

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            audio_path,
            "-f",
            "lavfi",
            "-t",
            f"{dur:.3f}",
            "-itsoffset",
            f"{args.offset_video}",
            "-i",
            f"color=c=black:s={args.width}x{args.height}",
            "-vf",
            sub_filter,
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            mp4_out,
        ]

        print(f"▶ Rendering to {mp4_out}")
        subprocess.run(cmd, check=True)

    print("[OK] wrote", mp4_out)

    # macOS: open output dir so Miguel doesn't have to dig
    if platform.system() == "Darwin":
        subprocess.run(["open", out_dir], check=False)


if __name__ == "__main__":
    main()
# end of render_from_csv.py
