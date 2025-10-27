#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Chrome emoji renderer → PNG frames → MP4
- 1920x1080 fixed canvas
- Centers lines horizontally and vertically
- Preserves explicit line breaks (\N -> \n) per slide
- Neutral spacing: no flex on the text node, no justify, word/letter spacing reset
- Requires: Google Chrome or Chromium in PATH (as "chrome", "chromium", or "google-chrome"), and ffmpeg
"""

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent if (HERE.name == "scripts") else HERE
OUT_DIR = PROJECT / "output" / "frames_chrome"
MP4_DIR = PROJECT / "output" / "chrome_rendered_mp4s"

HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html,body {{
    margin:0; padding:0; width:100%; height:100%;
    background:#000;
  }}
  /* Outer canvas forced to 1920x1080 for consistent screenshots */
  .canvas {{
    position:relative;
    width:1920px; height:1080px;
    background:#000;
    display:grid;
    place-items:center;
  }}
  /* A centered block that stacks lines */
  .vbox {{
    display:block;
    max-width: 86%;
    text-align:center;
  }}
  /* Each line is its own block to ensure no justification artifacts */
  .line {{
    display:block;
    margin: 22px 0;
    color:#fff;
    font-family: "Helvetica Neue", Helvetica, Arial, "Apple Color Emoji", "Noto Color Emoji", "Segoe UI Emoji", sans-serif;
    font-size:{font_size}px;
    line-height:1.20;
    letter-spacing: normal;
    word-spacing: normal;
    white-space: pre-wrap;   /* respect \n */
    text-align: center;
    text-rendering: optimizeLegibility;
    font-kerning: normal;
    -webkit-font-smoothing: antialiased;
    -webkit-text-size-adjust: 100%;
  }}
</style>
<div class="canvas">
  <div class="vbox">
{lines_html}
  </div>
</div>
"""

def find_chrome_binary() -> str:
    candidates = [
        "chrome",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in candidates:
        if shutil.which(c):
            return shutil.which(c)
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    print("FATAL: Chrome/Chromium binary not found. Install with: brew install --cask chromium", file=sys.stderr)
    sys.exit(1)

def assert_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("FATAL: ffmpeg not found. Install with: brew install ffmpeg", file=sys.stderr)
        sys.exit(1)

def read_lyrics(path: Path) -> list[str]:
    if not path.exists():
        print(f"FATAL: lyrics file not found: {path}", file=sys.stderr)
        sys.exit(1)
    raw = path.read_text(encoding="utf-8")
    # Normalize CRLF and convert literal \N to actual newlines
    raw = raw.replace("\r\n", "\n").replace("\\N", "\n")
    # Split into slides by blank line (one “screen” per non-empty paragraph block)
    blocks = [b.strip("\n") for b in re.split(r"\n{2,}", raw)]
    return [b for b in blocks if b.strip() != ""]

def render_slide_html(slide_text: str, font_size: int) -> str:
    # Each line in slide_text is already separated by real \n
    lines = slide_text.split("\n")
    lines_html = []
    for ln in lines:
        # Escape HTML but keep emoji as-is
        safe = html.escape(ln, quote=False)
        lines_html.append(f'    <div class="line">{safe}</div>')
    return HTML_TEMPLATE.format(font_size=font_size, lines_html="\n".join(lines_html))

def screenshot_frame(chrome_bin: str, html_str: str, out_png: Path):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as f:
        f.write(html_str.encode("utf-8"))
        tmp_html = f.name
    try:
        # Force a 1920x1080 viewport and capture PNG
        cmd = [
            chrome_bin,
            "--headless=new",
            "--disable-gpu",
            f"--window-size=1920,1080",
            f"--screenshot={str(out_png)}",
            f"file://{tmp_html}",
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.unlink(tmp_html)
        except OSError:
            pass

def encode_mp4_from_frames(pattern_glob: str, out_mp4: Path):
    # Ensure even dimensions and 1080p canvas without stretching content
    cmd = [
        "ffmpeg", "-y",
        "-framerate", "1/1.5",
        "-pattern_type", "glob", "-i", pattern_glob,
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v", "libx264", "-r", "30", "-pix_fmt", "yuv420p",
        str(out_mp4)
    ]
    subprocess.run(cmd, check=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--lyrics", required=True, help="Path to .txt")
    p.add_argument("--font-size", type=int, default=100)
    args = p.parse_args()

    chrome = find_chrome_binary()
    assert_ffmpeg()

    lyrics_path = Path(args.lyrics)
    slides = read_lyrics(lyrics_path)

    # Prepare output dirs
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MP4_DIR.mkdir(parents=True, exist_ok=True)

    # Render frames
    print("ℹ️ Rendering PNG frames with Chrome...")
    for i, slide in enumerate(slides, start=1):
        html_str = render_slide_html(slide, args.font_size)
        out_png = OUT_DIR / f"{i:03d}.png"
        screenshot_frame(chrome, html_str, out_png)
        print(f"  ✓ frame {i:03d}")

    # Encode MP4
    base = lyrics_path.stem
    out_mp4 = MP4_DIR / f"{base}_chrome_static.mp4"
    print("ℹ️ Encoding MP4...")
    encode_mp4_from_frames(str(OUT_DIR / "*.png"), out_mp4)
    print(f"✅ Done: {out_mp4}")

if __name__ == "__main__":
    main()
