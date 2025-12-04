#!/usr/bin/env python3
"""
4_mp4.py — Final MP4 renderer for Mixterioso

- 1920x1080, black background
- 3.5s title card using top/middle/bottom "bands":
    [Title band]
    [by/de band]
    [Artist band]
- Manual-timing CSV (line_index,time_secs,text)
- Current line in middle band
- "Next: ..." preview in bottom band
- Music-note rows (line_index < 0) shown as brief note pops
- --offset adjusts lyric timing (positive = later, negative = earlier)
"""

import sys
import os
import csv
import json
import subprocess
from pathlib import Path
import argparse

# ─────────────────────────────────────────────
# Bootstrap import path for mix_utils
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (  # type: ignore
    log, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS,
)

TXT_DIR  = PATHS["txt"]
TIM_DIR  = PATHS["timings"]
MIX_DIR  = PATHS["mixes"]
OUT_DIR  = PATHS["output"]
META_DIR = PATHS["meta"]

TITLE_DURATION = 3.5  # seconds
NOTE_DURATION  = 0.8  # length of music-note pops
LAST_LINE_EXTRA = 4.0 # how long last line stays on screen


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def sec_to_ass(t: float) -> str:
    """Convert seconds → ASS timestamp H:MM:SS.cc, clamped at 0."""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t - h * 3600 - m * 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    """Escape characters that annoy ASS."""
    return (text
            .replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
            .replace("\n", r"\N")
            .strip())


# ─────────────────────────────────────────────
# Load meta + timings
# ─────────────────────────────────────────────
def load_meta(slug: str) -> tuple[str, str]:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        log("META", f"Missing meta JSON: {meta_path}", RED)
        raise SystemExit("Need meta JSON (artist/title).")

    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        log("META", f"Failed to parse {meta_path}: {e}", RED)
        raise SystemExit("Bad meta JSON.")

    artist = (data.get("artist") or "").strip()
    title  = (data.get("title") or "").strip()
    if not artist or not title:
        raise SystemExit("Meta JSON missing artist/title.")
    return artist, title


def load_timings(slug: str):
    """
    Load timings CSV: line_index,time_secs,text

    Returns:
        notes: list of (time_secs, text) where line_index < 0
        lyrics: list of (time_secs, text) where line_index >= 0
    """
    csv_path = TIM_DIR / f"{slug}.csv"
    if not csv_path.exists():
        log("TIMING", f"Missing timings CSV: {csv_path}", RED)
        raise SystemExit("Need timings CSV for lyrics.")

    notes = []
    lyrics = []

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not {"line_index", "time_secs", "text"} <= set(reader.fieldnames or []):
            raise SystemExit(
                "Timings CSV must have header: line_index,time_secs,text"
            )

        for row in reader:
            try:
                idx = int(row["line_index"])
            except ValueError:
                # treat as non-lyric "note"
                idx = -1
            try:
                t = float(row["time_secs"])
            except ValueError:
                continue
            txt = (row.get("text") or "").strip()
            if not txt:
                continue

            if idx < 0:
                notes.append((t, txt))
            else:
                lyrics.append((t, txt))

    if not lyrics:
        raise SystemExit("No lyric rows (line_index >= 0) found in timings CSV.")

    # Sort by time just in case
    notes.sort(key=lambda x: x[0])
    lyrics.sort(key=lambda x: x[0])

    return notes, lyrics


# ─────────────────────────────────────────────
# ASS generation
# ─────────────────────────────────────────────
def build_ass(slug: str,
              artist: str,
              title: str,
              connector: str,
              offset: float,
              notes,
              lyrics) -> Path:
    """
    Build a single ASS file that contains:
      - Title card (0–TITLE_DURATION)
      - Notes (line_index < 0)
      - Current line in middle band
      - Next line preview in bottom band
    """
    ass_path = OUT_DIR / f"{slug}.ass"

    # Styles tuned for "band" layout on 1920x1080
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Title card styles
Style: TitleTop,Arial,96,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,0,8,80,80,120,1
Style: TitleConnector,Arial,64,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,0,5,80,80,40,1
Style: TitleBottom,Arial,80,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,0,2,80,80,160,1

; Lyric styles
Style: Current,Arial,80,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,110,110,0,0,1,4,0,5,80,80,80,1
Style: Next,Arial,64,&H0000FF00,&H00000000,&H00000000,&H64000000,-1,0,0,0,110,110,0,0,1,4,0,2,80,80,120,1
Style: Notes,Arial,72,&H00CCCCCC,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,4,0,5,80,80,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    # Title card (fixed time 0–TITLE_DURATION)
    top_txt = ass_escape(title)
    bot_txt = ass_escape(artist)
    conn_txt = ass_escape(connector)

    start_tc = sec_to_ass(0.0)
    end_tc   = sec_to_ass(TITLE_DURATION)
    fade_ms  = 300  # fade in/out

    events.append(
        f"Dialogue: 0,{start_tc},{end_tc},TitleTop,,0000,0000,0000,,{{\\fad({fade_ms},{fade_ms})}}{top_txt}"
    )
    events.append(
        f"Dialogue: 0,{start_tc},{end_tc},TitleConnector,,0000,0000,0000,,{{\\fad({fade_ms},{fade_ms})}}{conn_txt}"
    )
    events.append(
        f"Dialogue: 0,{start_tc},{end_tc},TitleBottom,,0000,0000,0000,,{{\\fad({fade_ms},{fade_ms})}}{bot_txt}"
    )

    # Notes (line_index < 0), shown as brief pops
    for t, txt in notes:
        s = sec_to_ass(t + offset)
        e = sec_to_ass(t + offset + NOTE_DURATION)
        events.append(
            f"Dialogue: 0,{s},{e},Notes,,0000,0000,0000,,{ass_escape(txt)}"
        )

    # Lyrics + Next preview (line_index >= 0)
    times = [t for (t, _) in lyrics]
    texts = [txt for (_, txt) in lyrics]

    for i, (t, txt) in enumerate(lyrics):
        start = t + offset
        if i + 1 < len(lyrics):
            end = times[i + 1] + offset
        else:
            end = t + offset + LAST_LINE_EXTRA

        if end <= start:
            end = start + 0.25

        s = sec_to_ass(start)
        e = sec_to_ass(end)
        cur_txt = ass_escape(texts[i])

        # Current line in middle band
        events.append(
            f"Dialogue: 0,{s},{e},Current,,0000,0000,0000,,{cur_txt}"
        )

        # Next preview (if there is a next line)
        if i + 1 < len(lyrics):
            next_txt = ass_escape(texts[i + 1])
            events.append(
                f"Dialogue: 0,{s},{e},Next,,0000,0000,0000,,Next: {next_txt}"
            )

    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log("ASS", f"Wrote ASS overlay: {ass_path}", GREEN)
    return ass_path


# ─────────────────────────────────────────────
# ffmpeg assembly
# ─────────────────────────────────────────────
def render_mp4(slug: str, offset: float, connector: str):
    """
    Final render:
      - color black 1920x1080 video
      - audio from mixes/<slug>.wav
      - draw subtle "bands" via ASS positioning (and optional boxes later)
      - overlay ASS (title card + lyrics)
    """
    mix_wav = MIX_DIR / f"{slug}.wav"
    if not mix_wav.exists():
        log("AUDIO", f"Missing mix WAV: {mix_wav}", RED)
        raise SystemExit("Missing WAV mix. Run 2_stems first.")

    # Load meta + timings
    artist, title = load_meta(slug)
    notes, lyrics = load_timings(slug)

    # Build ASS overlay
    ass_path = build_ass(slug, artist, title, connector, offset, notes, lyrics)

    out_path = OUT_DIR / f"{slug}.mp4"

    # We let audio drive duration via -shortest
    # Video: black background; overlay subtitles via ASS
    filter_complex = f"subtitles={ass_path}"

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", "color=c=black:size=1920x1080",
        "-i", str(mix_wav),
        "-filter_complex", filter_complex,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out_path),
    ]

    log("FFMPEG", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    log("OUT", f"Wrote video: {out_path}", GREEN)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Render final MP4 with title card + current/next lyrics."
    )
    p.add_argument("--slug", required=True, help="Song slug.")
    # Default offset may come from env, but CLI wins
    env_offset = os.getenv("KARAOKE_OFFSET_SECS")
    default_offset = float(env_offset) if env_offset not in (None, "") else 0.0
    p.add_argument(
        "--offset",
        type=float,
        default=default_offset,
        help="Timing offset for lyrics in seconds "
             "(+ = later text, - = earlier text).",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    # Offset confirmation
    print()
    print(f"Current lyrics offset: {args.offset:+.3f} seconds.")
    print("  Positive offset = lyrics appear later (delay the text).")
    print("  Negative offset = lyrics appear earlier (advance the text).")
    try:
        keep = input("Use this offset value? [Y/n]: ").strip().lower()
    except EOFError:
        keep = "y"

    offset = args.offset
    if keep in ("n", "no"):
        try:
            raw = input("Enter new offset in seconds (e.g. -1.50 for earlier): ").strip()
        except EOFError:
            raw = ""
        if raw:
            try:
                offset = float(raw)
            except ValueError:
                log("OFFSET", f"Invalid number '{raw}', keeping {args.offset:+.3f}s.", YELLOW)
                offset = args.offset
        log("OFFSET", f"Using offset {offset:+.3f}s", CYAN)
    else:
        log("OFFSET", f"Using offset {offset:+.3f}s", CYAN)

    # Language choice for connector
    print()
    print("Select language for title card connector:")
    print("  1) English: by")
    print("  2) Spanish: de")
    print("  3) Cancel")
    try:
        lang_choice = input("Choose [1-3]: ").strip()
    except EOFError:
        lang_choice = "1"

    if lang_choice == "1":
        connector = "by"
    elif lang_choice == "2":
        connector = "de"
    else:
        log("ABORT", "User cancelled MP4 render.", YELLOW)
        return

    render_mp4(slug, offset, connector)
    log("DONE", f"MP4 ready for upload: {OUT_DIR / f'{slug}.mp4'}", GREEN)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
