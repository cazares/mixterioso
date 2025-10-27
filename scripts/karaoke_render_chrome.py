#!/usr/bin/env python3
"""
karaoke_render_chrome.py
Generates static lyric slides (1920x1080) with emoji and text rendering.
Supports multiple rendering modes.
"""

import os
import sys
import argparse
import html
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------- Utility ----------

def html_escape(s: str) -> str:
    return html.escape(s)

def split_text_and_emojis(text: str):
    """Splits a string into (is_emoji, token) tuples."""
    import regex
    emoji_pattern = regex.compile(r'\X', regex.UNICODE)
    tokens = []
    for grapheme in emoji_pattern.findall(text):
        if any(ord(ch) > 10000 for ch in grapheme):
            tokens.append((True, grapheme))
        else:
            tokens.append((False, grapheme))
    return tokens

# ---------- Mode 4 (fixed) ----------

def html_for_mode4_placeholder_split(text: str, font_size: int) -> str:
    """
    Mode 4: baseline with hidden placeholders and absolutely positioned emoji spans.
    Supports word wrapping and centered alignment.
    """
    per_line = text.split("\n")
    emoji_overlays = []
    rendered_lines = []
    emoji_counter = 0
    char_w_px = int(font_size * 0.6)
    line_height_px = int(font_size * 1.1)

    for line_idx, line in enumerate(per_line):
        tokens = split_text_and_emojis(line)
        baseline_chunks = []
        col_index = 0
        for is_emo, tok in tokens:
            if is_emo:
                ph = f"[[E{emoji_counter}]]"
                baseline_chunks.append(f'<span class="ph">{ph}</span>')
                emoji_overlays.append({
                    "id": emoji_counter,
                    "emo": tok,
                    "line_idx": line_idx,
                    "col_index": col_index,
                })
                col_index += 2
                emoji_counter += 1
            else:
                baseline_chunks.append(html_escape(tok))
                col_index += len(tok)
        rendered_lines.append("".join(baseline_chunks))

    baseline_html = "<br/>".join(rendered_lines)
    emoji_span_html = []
    for e in emoji_overlays:
        x_px = e["col_index"] * char_w_px
        y_px = e["line_idx"] * line_height_px
        emoji_span_html.append(
            f'<div class="emo" style="left:{x_px}px; top:{y_px}px;">{html_escape(e["emo"])}</div>'
        )

    emoji_layer_html = "\n".join(emoji_span_html)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  html, body {{
    margin: 0;
    width: 1920px;
    height: 1080px;
    background: black;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .stage {{
    position: relative;
    width: 90%;
    max-width: 90%;
    color: white;
    text-align: center;
    font-size: {font_size}px;
    line-height: {line_height_px}px;
    font-family: Arial, sans-serif;
    word-wrap: break-word;
    white-space: pre-wrap;
  }}
  .ph {{
    visibility: hidden;
  }}
  .emo {{
    position: absolute;
    font-size: {font_size}px;
  }}
</style>
</head>
<body>
  <div class="stage">
    <div class="baseline">{baseline_html}</div>
    {emoji_layer_html}
  </div>
</body>
</html>
"""

# ---------- Rendering engine ----------

def generate_html_frames(lyrics_path: str, out_dir: str, mode: str, font_size: int):
    """Generate one HTML file per lyric line for Chrome capture."""
    os.makedirs(out_dir, exist_ok=True)
    lines = Path(lyrics_path).read_text(encoding="utf-8").splitlines()

    for i, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        html_content = html_for_mode4_placeholder_split(line, font_size)
        out_path = Path(out_dir) / f"slide_{i:03d}.html"
        out_path.write_text(html_content, encoding="utf-8")
        print(f"✅ wrote {out_path}")

    print(f"\nAll {len(lines)} slides ready at: {out_dir}")

# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Render karaoke slides with emoji support.")
    parser.add_argument("--lyrics", required=True, help="Path to lyrics text file")
    parser.add_argument("--font-size", type=int, default=100, help="Base font size")
    parser.add_argument("--modes", nargs="+", default=["mode4_placeholder_split"], help="Render modes to run")
    args = parser.parse_args()

    base_out_dir = Path("output/frames_chrome")
    for mode in args.modes:
        out_dir = base_out_dir / mode
        print(f"\n🎬 Rendering {mode} → {out_dir}")
        generate_html_frames(args.lyrics, out_dir, mode, args.font_size)

if __name__ == "__main__":
    main()
# end of karaoke_render_chrome.py
