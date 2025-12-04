#!/usr/bin/env python3
"""
Step 4: Render MP4 for Mixterioso.

- Requires slug
- Optional offset
- Uses timing CSV, lyrics TXT, and mixed WAV
- Outputs output/<slug>.mp4
"""

import sys
import subprocess
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import argparse

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS,
)


TXT_DIR   = PATHS["txt"]
TIM_DIR   = PATHS["timings"]
MIX_DIR   = PATHS["mixes"]
OUT_DIR   = PATHS["output"]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def build_ass_path(tmp: Path, slug: str) -> Path:
    return tmp / f"{slug}.ass"


def build_ass(slug: str, txt_path: Path, tim_path: Path, ass_path: Path, offset: float) -> None:
    """
    Build a minimal ASS subtitle from txt + timings CSV.
    ASS styling is not glamorous; minimal formatting only.
    """
    lines = txt_path.read_text(encoding="utf-8").splitlines()
    rows = tim_path.read_text(encoding="utf-8").splitlines()

    events = []
    for row in rows:
        if not row.strip():
            continue
        parts = row.split(",", 2)
        if len(parts) < 3:
            continue
        idx, sec, text = parts
        try:
            t = float(sec) + float(offset)
            if t < 0:
                t = 0.0
        except Exception:
            continue
        # ASS timestamps require h:mm:ss.xx
        mm = int(t // 60)
        ss = t % 60
        stamp = f"0:{mm:02d}:{ss:05.2f}"
        events.append((stamp, text))

    with ass_path.open("w", encoding="utf-8") as f:
        f.write("[Script Info]\n")
        f.write("ScriptType: v4.00+\n")
        f.write("PlayResX: 1920\n")
        f.write("PlayResY: 1080\n\n")

        f.write("[V4+ Styles]\n")
        f.write(
            "Style: Default,Arial,96,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
            "0,0,0,0,100,100,0,0,1,2,1,2,20,20,20,1\n\n"
        )

        f.write("[Events]\n")
        for stamp, text in events:
            safe = text.replace("\n", " ").replace(",", " ")
            f.write(f"Dialogue: 0,{stamp},{stamp},Default,,0,0,0,,{safe}\n")


def render_mp4(slug: str, offset: float) -> Path:
    """
    Create final MP4 using ffmpeg:
      - audio = mixes/<slug>.wav
      - subs  = generated .ass
      - black background + subtitles
    """
    txt_path = TXT_DIR / f"{slug}.txt"
    tim_path = TIM_DIR / f"{slug}.csv"
    wav_path = MIX_DIR / f"{slug}.wav"
    out_path = OUT_DIR / f"{slug}.mp4"

    if not txt_path.exists():
        raise SystemExit(f"Missing txt: {txt_path}")
    if not tim_path.exists():
        raise SystemExit(f"Missing timings CSV: {tim_path}")
    if not wav_path.exists():
        raise SystemExit(f"Missing mixed WAV: {wav_path}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tmp = OUT_DIR  # no need for scratch directory
    ass_path = build_ass_path(tmp, slug)
    build_ass(slug, txt_path, tim_path, ass_path, offset)

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:s=1920x1080:d=9999",
        "-i", str(wav_path),
        "-vf", f"subtitles='{ass_path}'",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]

    log("FFMPEG", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)

    log("MP4", f"Wrote {out_path}", GREEN)
    return out_path


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Render MP4 for Mixterioso.")
    p.add_argument("--slug", required=True, help="Slug (e.g. 'mujer_hilandera')")
    p.add_argument("--offset", type=float, default=0.0, help="Global timing offset (seconds)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    log("MODE", f"Rendering MP4 for slug='{slug}'", CYAN)

    out_path = render_mp4(slug, args.offset)

    print()
    log("DONE", f"Final MP4 at: {out_path}", GREEN)
    print()

    # Optional: offer to open output folder
    try:
        open_dir = input("Open output folder? [Y/n]: ").strip().lower()
    except EOFError:
        open_dir = "y"

    if open_dir in ("", "y", "yes"):
        subprocess.run(["open", str(OUT_DIR)])

    print()
    print("If you'd like to upload to YouTube next, run:")
    print(f"  python3 scripts/5_upload.py --slug {slug}")
    print()


if __name__ == "__main__":
    main()

# end of 4_mp4.py
