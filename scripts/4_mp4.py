#!/usr/bin/env python3
"""
4_mp4.py — Final MP4 renderer for Mixterioso
- Black background
- Title card for 3.5s (fade-in/out)
- English/Spanish connector: "by" / "de" (user-selected)
- Lyric ASS overlay for rest of video
- Keeps --offset for timing alignment
"""

import sys
import subprocess
from pathlib import Path
import argparse
import time

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

# ─────────────────────────────────────────────
# Build title card ASS file
# ─────────────────────────────────────────────
def create_title_ass(artist: str, title: str, connector: str, out_path: Path) -> None:
    """
    Generates a 3.5s ASS overlay:

        TITLE (large)
        
        by/de
        
        ARTIST (large)

    Uses same font family as lyric overlays. Fade-in/out included.
    """

    duration = 3.5
    fade = 0.3  # fade in/out

    # Large text
    # Matches your lyric styling: fontsize 120 * ASS scale typically 1.5
    ass = f"""[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,110,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,4,0,2,30,30,30,1
Style: Subtitle,Arial,80,&H00FFFFFF,&H00000000,&H00000000,&H64000000,-1,0,0,0,100,100,2,0,1,4,0,2,30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text

Dialogue: 0,0:00:00.00,0:00:{duration:.2f},Title,,0000,0000,0000,,{{\\fad({int(fade*1000)},{int(fade*1000)})}}{title.replace('{','[').replace('}',']')}

Dialogue: 0,0:00:00.00,0:00:{duration:.2f},Subtitle,,0000,0000,0200,,{{\\fad({int(fade*1000)},{int(fade*1000)})}}{connector}

Dialogue: 0,0:00:00.00,0:00:{duration:.2f},Title,,0000,0000,0400,,{{\\fad({int(fade*1000)},{int(fade*1000)})}}{artist.replace('{','[').replace('}',']')}
"""

    out_path.write_text(ass, encoding="utf-8")
    log("ASS", f"Created title card ASS: {out_path}", GREEN)


# ─────────────────────────────────────────────
# Build lyric ASS overlay (already created elsewhere)
# ─────────────────────────────────────────────
def load_lyric_ass(slug: str) -> Path:
    """
    You already generate an ASS file inside 4_mp4 logic (lyrics + timeline).
    For now we assume it's at: output/<slug>_lyrics.ass
    If your existing pipeline builds ASS differently, you can adapt here.
    """
    ass_path = OUT_DIR / f"{slug}_lyrics.ass"
    if not ass_path.exists():
        log("ASS", f"No lyric ASS found at {ass_path}", RED)
        raise SystemExit("Missing lyric ASS.")
    return ass_path


# ─────────────────────────────────────────────
# ffmpeg assembly
# ─────────────────────────────────────────────
def render_mp4(slug: str, offset: float, connector: str, artist: str, title: str):
    """
    Final render:
    - black background 1920x1080
    - title card ASS (0–3.5s)
    - lyric ASS (starting after 3.5s)
    - audio = mixes/<slug>.wav
    """

    mix_wav = MIX_DIR / f"{slug}.wav"
    if not mix_wav.exists():
        log("AUDIO", f"Missing mix WAV: {mix_wav}", RED)
        raise SystemExit("Missing WAV mix.")

    # Build title card ASS
    title_ass_path = OUT_DIR / f"{slug}_title.ass"
    create_title_ass(artist, title, connector, title_ass_path)

    # lyric ASS (already built)
    lyric_ass_path = load_lyric_ass(slug)

    # output final mp4
    out_path = OUT_DIR / f"{slug}.mp4"

    # Offset for lyrics (shift ASS timing)
    # Negative offset moves lyrics earlier, positive later
    offset_filter = f"ass={lyric_ass_path}:original=1:delay={int(offset*1000)}"

    # Compose filter:
    # 1. main black background
    # 2. title_card overlay for first 3.5s
    # 3. lyric overlay
    filter_complex = (
        f"[0:v]trim=0:3.5,setpts=PTS-STARTPTS[vbg0];"
        f"[0:v]trim=3.5,setpts=PTS-STARTPTS[vbg1];"
        f"[vbg0]ass={title_ass_path}[v0];"
        f"[vbg1]{offset_filter}[v1];"
        f"[v0][v1]concat=n=2:v=1:a=0[vout]"
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

    log("FFMPEG", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)

    log("OUT", f"Wrote video: {out_path}", GREEN)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Render final MP4 with title card + lyrics.")
    p.add_argument("--slug", required=True, help="Song slug.")
    p.add_argument("--offset", type=float, default=0.0, help="Timing offset (seconds).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = slugify(args.slug)

    # Load meta for artist/title
    meta_path = PATHS["meta"] / f"{slug}.json"
    if not meta_path.exists():
        log("META", f"Missing meta JSON: {meta_path}", RED)
        raise SystemExit("Need meta JSON to extract artist/title.")

    meta = meta_path.read_text(encoding="utf-8")
    import json
    meta = json.loads(meta)

    artist = meta.get("artist", "").strip()
    title  = meta.get("title", "").strip()

    if not artist or not title:
        raise SystemExit("Meta missing artist/title.")

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
        log("ABORT", "User cancelled.", YELLOW)
        return

    render_mp4(slug, args.offset, connector, artist, title)

    log("DONE", f"MP4 ready for upload: {OUT_DIR / f'{slug}.mp4'}", GREEN)


if __name__ == "__main__":
    main()

# end of 4_mp4.py
