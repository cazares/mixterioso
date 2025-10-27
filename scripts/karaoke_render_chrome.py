#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_render_chrome.py
Generate 1920x1080 PNG slides for each lyric "screen" with full emoji support.

Key behavior:
- Each NON-EMPTY line in the lyrics file becomes ONE slide.
- Literal "\\N" in that line becomes real newlines in that slide.
- Text is centered both horizontally and vertically on 1920x1080 black.
- We support multiple render modes to fight Chrome emoji spacing bugs.
- We screenshot with headless Chromium.

Usage:
  python3 karaoke_render_chrome.py --lyrics lyrics/veinte_roses.txt --font-size 100 --modes all

Output:
  output/frames_chrome/<mode_name>/<###>.png
"""

import os
import sys
import re
import argparse
import tempfile
import subprocess
from pathlib import Path
from html import escape as html_escape

CHROMIUM_BIN = "/Applications/Chromium.app/Contents/MacOS/Chromium"


def resolve_lyrics_path(path_str: str) -> Path:
    """Find lyrics file even if placed in lyrics/ or scripts/lyrics/, absolute or relative."""
    p = Path(path_str).expanduser()
    if p.exists():
        return p.resolve()

    base = Path(__file__).resolve().parent.parent
    name = Path(path_str).name

    alt_paths = [
        base / "lyrics" / name,
        base / "scripts" / "lyrics" / name,
    ]
    for alt in alt_paths:
        if alt.exists():
            return alt.resolve()

    print(f"FATAL: lyrics file not found in any known location: {path_str}", file=sys.stderr)
    print(f"Checked: {[str(a) for a in alt_paths]}", file=sys.stderr)
    sys.exit(1)


def normalize_newlines(raw_line: str) -> str:
    """Convert literal \\N sequences into real newlines preserving multiples.
    'aaa\\N\\Nbbb' -> 'aaa\n\nbbb'
    We treat every '\\N' as '\n'.
    """
    return raw_line.replace("\\N", "\n")


def make_lines_array(lyrics_path: Path):
    raw_lines = lyrics_path.read_text(encoding="utf-8").splitlines()
    slides = []
    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        slides.append(normalize_newlines(stripped))
    return slides


def html_for_mode1_textonly(text: str, font_size: int) -> str:
    """
    Mode 1: simple flex centering + pre-line.
    Attempts to kill extra spacing with letter-spacing / word-spacing.
    """
    safe_text = html_escape(text)
    return f"""<!DOCTYPE html>
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
    letter-spacing: 0;
    word-spacing: 0;
  }}
  .wrapper {{
    max-width: 90%;
    line-height: 1.1;
    font-size: {font_size}px;
    white-space: pre-line;
    word-wrap: break-word;
    overflow-wrap: break-word;
  }}
</style>
</head>
<body>
<div class="wrapper">{safe_text}</div>
</body>
</html>"""


def html_for_mode2_css_tight(text: str, font_size: int) -> str:
    """
    Mode 2: manual <br> injection + inline-block span with white-space:pre.
    We block Chrome from reflowing emoji clusters by preventing wrapping.
    Good for dense emoji walls like 🌹🌹🌹 lines.
    """
    # convert \n to <br/>
    parts = text.split("\n")
    html_lines = "<br/>".join(html_escape(p) for p in parts)

    return f"""<!DOCTYPE html>
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
    text-align: center;
  }}
  .wrapper {{
    color: white;
    font-size: {font_size}px;
    font-family: "Apple Color Emoji","Noto Color Emoji","Arial Unicode MS",Arial,sans-serif;
    line-height: 1.1;
    display: inline-block;
    white-space: pre;
    letter-spacing: 0;
    word-spacing: 0;
    max-width: 90%;
  }}
</style>
</head>
<body>
<div class="wrapper">{html_lines}</div>
</body>
</html>"""


def html_for_mode3_spanwrap(text: str, font_size: int) -> str:
    """
    Mode 3: break each "word" (sequences separated by spaces) into <span class=chunk>.
    chunk is display:inline-block so Chrome won't stretch inter-word gaps.
    newline -> <br/>.
    """
    lines = text.split("\n")
    out_lines = []
    for line in lines:
        words = line.split(" ")
        spans = []
        for w in words:
            if w == "":
                # preserve double-spaces by inserting a thin-space span
                spans.append('<span class="chunk">&nbsp;</span>')
            else:
                spans.append(f'<span class="chunk">{html_escape(w)}</span>')
        out_lines.append(" ".join(spans))
    html_body = "<br/>".join(out_lines)

    return f"""<!DOCTYPE html>
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
    text-align: center;
  }}
  .outer {{
    max-width: 90%;
    color: white;
    font-size: {font_size}px;
    font-family: "Apple Color Emoji","Noto Color Emoji","Arial Unicode MS",Arial,sans-serif;
    line-height: 1.1;
    text-align: center;
    letter-spacing: 0;
    word-spacing: 0;
  }}
  .chunk {{
    display: inline-block;
    white-space: pre;
  }}
</style>
</head>
<body>
<div class="outer">{html_body}</div>
</body>
</html>"""


def split_text_and_emojis(s: str):
    """
    Mode 4 helper.
    Returns list of (is_emoji, token).
    Very lightweight heuristic:
      treat any codepoint >= 0x1F300 as emoji-ish.
    """
    out = []
    buf = []
    def flush_buf():
        if buf:
            out.append((False, "".join(buf)))
            buf.clear()

    for ch in s:
        if ord(ch) >= 0x1F300:
            flush_buf()
            out.append((True, ch))
        else:
            buf.append(ch)
    flush_buf()
    return out


def html_for_mode4_placeholder_split(text: str, font_size: int) -> str:
    """
    Mode 4: text baseline with placeholders + absolutely positioned emoji spans.
    Approximate x offset for emoji.

    We draw each line separately stacked vertically.
    We'll compute x offsets by counting characters at ~0.6 * font_size px each.
    This is heuristic but works fine for short karaoke lines.
    """
    per_line = text.split("\n")

    # build rows of baseline text with placeholders like [[E0]]
    # and a list of emoji overlay objects {emo, line_idx, ch_index}
    emoji_overlays = []
    rendered_lines = []
    emoji_counter = 0

    for line_idx, line in enumerate(per_line):
        tokens = split_text_and_emojis(line)
        baseline_chunks = []
        col_index = 0
        for is_emo, tok in tokens:
            if is_emo:
                ph = f"[[E{emoji_counter}]]"
                baseline_chunks.append(ph)
                emoji_overlays.append({
                    "id": emoji_counter,
                    "emo": tok,
                    "line_idx": line_idx,
                    "col_index": col_index,
                })
                # treat emoji as width 2 columns for spacing guess
                col_index += 2
                emoji_counter += 1
            else:
                baseline_chunks.append(tok)
                col_index += len(tok)
        rendered_lines.append("".join(baseline_chunks))

    # approximate metrics
    line_height_px = int(font_size * 1.1)
    char_w_px = int(font_size * 0.6)

    # build emoji absolutely positioned spans
    emoji_span_html = []
    for e in emoji_overlays:
        x_px = e["col_index"] * char_w_px
        y_px = e["line_idx"] * line_height_px
        emoji_span_html.append(
            f'<div class="emo" style="left:{x_px}px; top:{y_px}px;">{html_escape(e["emo"])}</div>'
        )

    # baseline text block (placeholders still visible; we hide them with CSS)
    baseline_html = "<br/>".join(html_escape(l) for l in rendered_lines)
    emoji_layer_html = "\n".join(emoji_span_html)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  body {{
    margin: 0;
    padding: 0;
    width: 1920px;
    height: 1080px;
    background: black;
    color: white;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .stage {{
    position: relative;
    width: 90%;
    max-width: 90%;
    color: white;
    font-family: Arial, sans-serif;
    font-size: {font_size}px;
    line-height: {line_height_px}px;
    text-align: center;
    letter-spacing: 0;
    word-spacing: 0;
    white-space: pre;
  }}
  .baseline {{
    color: white;
    visibility: visible;
  }}
  /* hide placeholders like [[E0]] */
  .baseline {{
    font-family: Arial, sans-serif;
  }}
  .baseline .ph {{
    visibility: hidden;
  }}
  .emo {{
    font-family: "Apple Color Emoji","Noto Color Emoji","Arial Unicode MS",sans-serif;
    font-size: {font_size}px;
    line-height: {line_height_px}px;
    position: absolute;
    transform: translate(-50%, 0);
    white-space: pre;
  }}
</style>
</head>
<body>
<div class="stage">
  <div class="baseline">{baseline_html}</div>
  {emoji_layer_html}
</div>
</body>
</html>"""


def html_for_mode5_canvas(text: str, font_size: int) -> str:
    """
    Mode 5: draw on <canvas> with JS using fillText.
    We control layout manually. This often kills weird spacing.
    """
    lines_js_array = [l for l in text.split("\n")]
    js_lines = ",\n      ".join([repr(l) for l in lines_js_array])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  body {{
    margin: 0;
    background: black;
    width: 1920px;
    height: 1080px;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  canvas {{
    background: black;
    width: 1920px;
    height: 1080px;
  }}
</style>
</head>
<body>
<canvas id="c" width="1920" height="1080"></canvas>
<script>
(function() {{
  const lines = [
      {js_lines}
  ];
  const c = document.getElementById('c');
  const ctx = c.getContext('2d');
  ctx.fillStyle = 'white';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  ctx.font = "{font_size}px 'Apple Color Emoji','Noto Color Emoji','Arial Unicode MS',sans-serif";
  const lineH = {font_size} * 1.1;
  const totalH = lineH * lines.length;
  let y0 = (1080 - totalH)/2;
  for (let i=0; i<lines.length; i++) {{
    const t = lines[i];
    ctx.fillText(t, 1920/2, y0 + i*lineH);
  }}
}})();
</script>
</body>
</html>"""


def build_html(mode_name: str, text: str, font_size: int) -> str:
    if mode_name == "mode1_textonly":
        return html_for_mode1_textonly(text, font_size)
    if mode_name == "mode2_css_tight":
        return html_for_mode2_css_tight(text, font_size)
    if mode_name == "mode3_spanwrap":
        return html_for_mode3_spanwrap(text, font_size)
    if mode_name == "mode4_placeholder_split":
        return html_for_mode4_placeholder_split(text, font_size)
    if mode_name == "mode5_canvas":
        return html_for_mode5_canvas(text, font_size)
    # fallback
    return html_for_mode1_textonly(text, font_size)


def screenshot_html_to_png(html_str: str, out_path: Path):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
    tmp.write(html_str.encode("utf-8"))
    tmp.close()

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

    print(f"[chrome:{out_path.parent.name}] wrote frame {out_path}")


def main():
    ap = argparse.ArgumentParser(description="Render emoji-safe lyric slides via headless Chromium. Multiple modes.")
    ap.add_argument("--lyrics", required=True,
                    help="Path to lyrics .txt (each non-empty line = one screen). Literal \\N becomes newline.")
    ap.add_argument("--font-size", type=int, default=100,
                    help="Text font size in px. Default 100.")
    ap.add_argument("--modes", default="mode1_textonly",
                    help="Comma-separated list of modes or 'all'. "
                         "Options: mode1_textonly,mode2_css_tight,mode3_spanwrap,mode4_placeholder_split,mode5_canvas,all")
    args = ap.parse_args()

    lyrics_path = resolve_lyrics_path(args.lyrics)
    print(f"[chrome] Using lyrics file: {lyrics_path}")

    slides = make_lines_array(lyrics_path)

    if args.modes == "all":
        modes = [
            "mode1_textonly",
            "mode2_css_tight",
            "mode3_spanwrap",
            "mode4_placeholder_split",
            "mode5_canvas",
        ]
    else:
        modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    base_frames = Path("output/frames_chrome")
    for mode_name in modes:
        mode_dir = base_frames / mode_name
        mode_dir.mkdir(parents=True, exist_ok=True)

        for idx, slide_text in enumerate(slides, start=1):
            html_str = build_html(mode_name, slide_text, args.font_size)
            out_png = mode_dir / f"{idx:03d}.png"
            screenshot_html_to_png(html_str, out_png)

    print("[chrome] done generating PNG slides in:")
    for mode_name in modes:
        print(f"  output/frames_chrome/{mode_name}/")


if __name__ == "__main__":
    main()

# end of karaoke_render_chrome.py
