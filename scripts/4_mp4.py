#!/usr/bin/env python3
"""
4_mp4.py — Final MP4 renderer for Mixterioso
Implements:
- Title card (3.5s) with English/Spanish connector
- Unified lyric + note timing logic (D1)
- Notes keep distinct visual style but behave like lyrics
- Notes DO NOT receive 'Next:' previews (N)
- Offset prompt with explanation
- Black background, 1920x1080
"""

import sys
import subprocess
import argparse
import time
from pathlib import Path
import json

# ─────────────────────────────────────────────
# Bootstrap import path for mix_utils
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS
)

TXT_DIR  = PATHS["txt"]
TIM_DIR  = PATHS["timings"]
MIX_DIR  = PATHS["mixes"]
OUT_DIR  = PATHS["output"]
META_DIR = PATHS["meta"]

TITLE_DURATION = 3.5
FADE = 0.20  # fade in/out for title card (ms = ×1000 internally)


# ─────────────────────────────────────────────
# Title Card ASS
# ─────────────────────────────────────────────
def create_title_ass(artist: str, title: str, connector: str, out_path: Path) -> None:
    """
    Title card for first 3.5s:
        TITLE
        by/de
        ARTIST
    """

    fade_ms = int(FADE * 1000)

    ass = f"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,110,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,3,0,1,5,0,2,40,40,40,1
Style: Subtitle,Arial,80,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,3,0,1,5,0,2,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:{TITLE_DURATION:.2f},Title,,0000,0000,0000,,{{\\fad({fade_ms},{fade_ms})}}{title}
Dialogue: 0,0:00:00.00,0:00:{TITLE_DURATION:.2f},Subtitle,,0000,0000,0200,,{{\\fad({fade_ms},{fade_ms})}}{connector}
Dialogue: 0,0:00:00.00,0:00:{TITLE_DURATION:.2f},Title,,0000,0000,0400,,{{\\fad({fade_ms},{fade_ms})}}{artist}
"""
    out_path.write_text(ass, encoding="utf-8")
    log("ASS", f"Created title card ASS: {out_path}", GREEN)


# ─────────────────────────────────────────────
# Lyric + Notes ASS Builder (D1 + N)
# ─────────────────────────────────────────────
def build_lyrics_ass(slug: str, offset: float) -> Path:
    """
    Build unified ASS overlay for lyrics + note lines.
    Notes (line_index < 0) use NoteStyle but behave EXACTLY like lyrics:
        - Same interpolation
        - Same fade
        - Same offset
        - NO 'Next:' preview (N)
    """

    timings_csv = TIM_DIR / f"{slug}.csv"
    txt_file    = TXT_DIR / f"{slug}.txt"
    ass_path    = OUT_DIR / f"{slug}_lyrics.ass"

    if not timings_csv.exists():
        log("TIM", f"Missing timings CSV: {timings_csv}", RED)
        raise SystemExit("Missing timings CSV.")

    # Load lines
    rows = []
    for line in timings_csv.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("line_index"):
            continue
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        idx = int(parts[0])
        t   = float(parts[1])
        txt = parts[2].strip()
        rows.append((idx, t, txt))

    if not rows:
        raise SystemExit("Timings CSV contains no rows.")

    # Interpolate end times:
    # last line gets +2.0s default
    interpolated = []
    for i, (idx, t, txt) in enumerate(rows):
        if i < len(rows) - 1:
            t_next = rows[i+1][1]
            end = t_next - 0.10
        else:
            end = t + 2.0
        interpolated.append((idx, t, end, txt))

    # Offset shift
    for i in range(len(interpolated)):
        idx, t0, t1, txt = interpolated[i]
        interpolated[i] = (idx, t0 + offset, t1 + offset, txt)

    # ASS writing
    fade_ms = 150

    ass = []
    ass.append("[Script Info]")
    ass.append("ScriptType: v4.00+\n")

    ass.append("[V4+ Styles]")
    ass.append(
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )

    # Lyrics
    ass.append("Style: LyricTop,Arial,80,&H00FFFFFF,&H00000000,&H00000000,&H64000000,"
               "-1,0,0,0,100,100,2,0,1,4,0,2,40,40,40,1")

    # Notes distinct style
    ass.append("Style: NoteStyle,Arial,80,&H00FFFF00,&H00000000,&H00000000,&H64000000,"
               "-1,0,0,0,100,100,2,0,1,4,0,2,40,40,40,1")

    # Preview style
    ass.append("Style: NextStyle,Arial,60,&H0080FF80,&H00000000,&H00000000,&H64000000,"
               "-1,0,0,0,100,100,2,0,1,4,0,2,40,40,40,1\n")

    ass.append("[Events]")
    ass.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    # Write lines
    for i, (idx, t0, t1, txt) in enumerate(interpolated):
        start = max(0.0, t0)
        end   = max(start + 0.05, t1)

        style = "NoteStyle" if idx < 0 else "LyricTop"
        clean_txt = txt.replace("{", "[").replace("}", "]")
        ass.append(
            f"Dialogue: 0,{fmt_ts(start)},{fmt_ts(end)},{style},,0000,0000,0000,,"
            f"{{\\fad({fade_ms},{fade_ms})}}{clean_txt}"
        )

        # NEXT PREVIEW — ONLY FOR NORMAL LINES
        if idx >= 0:
            if i < len(interpolated) - 1:
                _, _, _, next_txt = interpolated[i+1]
                next_clean = next_txt.replace("{", "[").replace("}", "]")
                prev_start = max(0, start - 1.0)
                prev_end   = start - 0.05
                if prev_end > prev_start:
                    ass.append(
                        f"Dialogue: 1,{fmt_ts(prev_start)},{fmt_ts(prev_end)},NextStyle,,0000,0000,0000,,"
                        f"{{\\fad({fade_ms},{fade_ms})}}Next: {next_clean}"
                    )

    ass_path.write_text("\n".join(ass), encoding="utf-8")
    log("ASS", f"Wrote lyric/note ASS: {ass_path}", GREEN)
    return ass_path


# ─────────────────────────────────────────────
# Timecode helper
# ─────────────────────────────────────────────
def fmt_ts(t: float) -> str:
    if t < 0:
        t = 0
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    return f"{h:d}:{m:02d}:{s:06.3f}"


# ─────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────
def render_mp4(slug: str, offset: float, connector: str, artist: str, title: str):
    mix_wav = MIX_DIR / f"{slug}.wav"
    if not mix_wav.exists():
        log("AUDIO", f"Missing WAV: {mix_wav}", RED)
        raise SystemExit("Missing mix WAV.")

    title_ass = OUT_DIR / f"{slug}_title.ass"
    create_title_ass(artist, title, connector, title_ass)

    lyrics_ass = build_lyrics_ass(slug, offset)

    out_path = OUT_DIR / f"{slug}.mp4"

    # Build filter
    filter_complex = (
        f"[0:v]trim=0:{TITLE_DURATION},setpts=PTS-STARTPTS[v0];"
        f"[0:v]trim={TITLE_DURATION},setpts=PTS-STARTPTS[v1];"
        f"[v0]ass={title_ass}[vtc];"
        f"[v1]ass={lyrics_ass}[vlyr];"
        f"[vtc][vlyr]concat=n=2:v=1:a=0[vout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:size=1920x1080",
        "-i", str(mix_wav),
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_path)
    ]

    log("FF", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    log("DONE", f"Wrote MP4: {out_path}", GREEN)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Render final MP4.")
    p.add_argument("--slug", required=True)
    p.add_argument("--offset", type=float, default=0.0)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    # Load meta
    meta_file = META_DIR / f"{slug}.json"
    if not meta_file.exists():
        log("META", f"Missing meta JSON: {meta_file}", RED)
        raise SystemExit("Missing meta JSON.")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))

    artist = meta.get("artist", "").strip()
    title  = meta.get("title", "").strip()
    if not artist or not title:
        raise SystemExit("Meta missing artist/title.")

    # Confirm offset
    print()
    print(f"Current lyrics offset: {args.offset:+.3f} seconds.")
    print("  Positive = lyrics appear later (delayed).")
    print("  Negative = lyrics appear earlier (advanced).")
    if input("Use this offset? [Y/n]: ").strip().lower() == "n":
        try:
            new_off = float(input("Enter new offset (e.g. -1.50): ").strip())
            args.offset = new_off
            print(f"[OFFSET] Using offset {args.offset:+.3f}s")
        except Exception:
            print("Invalid offset, keeping original.")

    # Connector
    print()
    print("Select title-card language:")
    print("  1) English: by")
    print("  2) Spanish: de")
    print("  3) Cancel")
    choice = input("Choose [1-3]: ").strip()
    if choice == "2":
        connector = "de"
    elif choice == "1":
        connector = "by"
    else:
        log("ABORT", "User cancelled.", YELLOW)
        return

    render_mp4(slug, args.offset, connector, artist, title)


if __name__ == "__main__":
    main()
# end of 4_mp4.py
