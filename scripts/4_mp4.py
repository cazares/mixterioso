#!/usr/bin/env python3
import sys
import csv
import subprocess
from pathlib import Path
import argparse

# ─────────────────────────────────────────────
# Bootstrap sys.path
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    PATHS, slugify
)

TXT_DIR = PATHS["txt"]
MP3_DIR = PATHS["mp3"]
TIM_DIR = PATHS["timings"]
MIX_DIR = PATHS["mixes"]
OUT_DIR = PATHS["output"]

# ─────────────────────────────────────────────
# Read timings CSV
# ─────────────────────────────────────────────
def load_timings(slug: str) -> list[tuple[int, float, str]]:
    csv_path = TIM_DIR / f"{slug}.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing timings CSV: {csv_path}")

    rows = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        # Expected header: line_index,time_secs,text
        for row in reader:
            if len(row) < 3:
                continue
            idx = int(row[0])
            ts  = float(row[1])
            txt = row[2]
            rows.append((idx, ts, txt))
    return rows

# ─────────────────────────────────────────────
# Embed ASS (simple, single-style)
# ─────────────────────────────────────────────
def build_ass(slug: str, lyrics: list[tuple[int, float, str]]) -> Path:
    """
    Generate ASS file with:
    - divider bar
    - up-next preview
    - top/bottom bands from LKWV
    - single karaoke style
    - deterministic styling
    """

    ass_path = OUT_DIR / f"{slug}.ass"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    style = (
        "Style: KARAOKE,Arial,120,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1"
    )

    # Divider bar as separate style
    style_div = (
        "Style: DIVIDER,Arial,20,&H00FFFFFF,&H00000000,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1"
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "",
        "[V4+ Styles]",
        style,
        style_div,
        "",
        "[Events]"
    ]

    def fmt_ts(sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"

    # Add divider + lyrics + up-next preview
    for i, (idx, ts, text) in enumerate(lyrics):
        start = fmt_ts(ts)
        end   = fmt_ts(ts + 5)  # each line visible ~5s

        # divider bar
        lines.append(
            f"Dialogue: 0,DIVIDER,,0,0,0,,{{\\an2}}----------------------------------------"
        )

        # current line
        lines.append(
            f"Dialogue: 0,KARAOKE,,0,0,0,,{{\\an8}}{text}"
        )

        # next line preview
        if i + 1 < len(lyrics):
            nxt = lyrics[i + 1][2]
            lines.append(
                f"Dialogue: 0,KARAOKE,,0,0,0,,{{\\an2}}Next: {nxt}"
            )
        else:
            lines.append(
                f"Dialogue: 0,KARAOKE,,0,0,0,,{{\\an2}}"
            )

    ass_path.write_text("\n".join(lines), encoding="utf-8")
    return ass_path

# ─────────────────────────────────────────────
# MP4 render
# ─────────────────────────────────────────────
def render_mp4(slug: str, offset: float):
    """
    Combine:
    - Audio from mixes/<slug>.wav
    - Subtitles from ASS
    - Create output/<slug>.mp4
    """

    wav = MIX_DIR / f"{slug}.wav"
    if not wav.exists():
        raise SystemExit(f"Missing mixed WAV: {wav}")

    txt = TXT_DIR / f"{slug}.txt"
    if not txt.exists():
        raise SystemExit(f"Missing lyrics txt: {txt}")

    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        raise SystemExit(f"Missing mp3: {mp3}")

    timings = TIM_DIR / f"{slug}.csv"
    if not timings.exists():
        raise SystemExit(f"Missing timings CSV: {timings}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ass_path = OUT_DIR / f"{slug}.ass"
    mp4_path = OUT_DIR / f"{slug}.mp4"

    # Read timings
    rows = load_timings(slug)

    # Apply offset
    if offset != 0:
        rows = [(idx, ts + offset, text) for (idx, ts, text) in rows]

    # Build ASS file
    ass = build_ass(slug, rows)

    # Render with ffmpeg
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav),
        "-vf", f"ass={ass}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(mp4_path)
    ]

    log("FFMPEG", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    log("MP4", f"Wrote: {mp4_path}", GREEN)

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Final MP4 renderer (simple single-mode).")
    p.add_argument("--slug", required=True)
    p.add_argument("--offset", type=float, default=0.0, help="Time offset (seconds, can be negative).")
    args = p.parse_args()

    slug = slugify(args.slug)
    render_mp4(slug, args.offset)

if __name__ == "__main__":
    main()
# end of 4_mp4.py
