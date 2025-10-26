#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_render_chrome.py
Generate 1920x1080 PNG slides for each lyric "screen" with full Apple/Chrome emoji rendering.

Rules:
- Each NON-EMPTY line in the lyrics file becomes ONE slide.
- Inside that line, literal "\\N" becomes a real newline in that slide.
- Output frames go to output/frames_chrome/*.png
- Text is centered horizontally and vertically, wrapped, white on black
- Font size defaults to 100px, overridable with --font-size
- We use system Chromium headless. Assumes Chromium is installed at:
  /Applications/Chromium.app/Contents/MacOS/Chromium
"""

import os
import sys
import argparse
import tempfile
import subprocess
from pathlib import Path

# Path to Chromium browser executable (headless mode)
CHROMIUM_BIN = "/Applications/Chromium.app/Contents/MacOS/Chromium"


def resolve_lyrics_path(path_str: str) -> Path:
    """Find lyrics file even if placed in lyrics/ or scripts/lyrics/."""
    p = Path(path_str).expanduser().resolve()
    if p.exists():
        return p

    base = Path.cwd()
    alt_paths = [
        base / "lyrics" / Path(path_str).name,
        base / "scripts" / "lyrics" / Path(path_str).name,
    ]
    for alt in alt_paths:
        if alt.exists():
            return alt.resolve()

    print(f"FATAL: lyrics file not found in any known location: {path_str}", file=sys.stderr)
    sys.exit(1)


def render_line_to_png(line_text: str, index: int, out_dir: Path, font_size: int):
    """
    Render one lyric line (already processed with .replace("\\N", "\n"))
    into a centered 1920x1080 PNG using headless Chromium.
    """
    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  html, body {{
    margin: 0;
    padding: 0;
    width: 1920px;
    height: 1080px;
    background: black;
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: "Apple Color Emoji","Noto Color Emoji","Arial Unicode MS",Arial,sans-serif;
    text-align: center;
  }}
  .wrapper {{
    max-width: 90%;
    line-height: 1.25;
    font-size: {font_size}px;
    white-space: pre-line;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }}
</style>
</head>
<body>
<div class="wrapper">{line_text}</div>
</body>
</html>"""

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    tmp.write(html.encode("utf-8"))
    tmp.close()

    out_path = out_dir / f"{index:03d}.png"

    cmd = [
        CHROMIUM_BIN,
        "--headless",
        "--disable-gpu",
        f"--screenshot={out_path}",
        f"file://{tmp.name}",
        "--window-size=1920,1080",
    ]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print(f"FATAL: Chromium binary not found at {CHROMIUM_BIN}", file=sys.stderr)
        print("Install via: brew install --cask chromium", file=sys.stderr)
        print("Then run: sudo xattr -rd com.apple.quarantine /Applications/Chromium.app", file=sys.stderr)
        sys.exit(1)
    finally:
        os.unlink(tmp.name)

    print(f"[chrome] wrote frame {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Render emoji-safe lyric slides via headless Chromium.")
    ap.add_argument("--lyrics", required=True,
                    help="Path to lyrics .txt (each non-empty line = one screen). Literal \\N becomes newline.")
    ap.add_argument("--font-size", type=int, default=100,
                    help="Text font size in px. Default 100.")
    args = ap.parse_args()

    lyrics_path = resolve_lyrics_path(args.lyrics)
    print(f"[chrome] Using lyrics file: {lyrics_path}")

    frames_dir = Path("output/frames_chrome")
    frames_dir.mkdir(parents=True, exist_ok=True)

    raw_lines = lyrics_path.read_text(encoding="utf-8").splitlines()

    slide_texts = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        slide_texts.append(stripped.replace("\\N", "\n"))

    for idx, slide in enumerate(slide_texts, start=1):
        render_line_to_png(
            line_text=slide,
            index=idx,
            out_dir=frames_dir,
            font_size=args.font_size,
        )

    print("[chrome] done generating PNG slides in output/frames_chrome")


if __name__ == "__main__":
    main()

# end of karaoke_render_chrome.py
