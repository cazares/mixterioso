#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_emoji.py – render per-line emoji strips to PNG and generate ffmpeg overlay filters.

New version (grid layout + scale factor):
- Each line is split by \N into stacked rows.
- Each character is rendered separately if it’s an emoji (Twemoji PNG).
- font_px acts as scale factor; emojis scale proportionally.
- Centered horizontally and vertically on the video canvas.
"""

from pathlib import Path
from typing import List, Tuple
from PIL import Image
import math
import emoji
import karaoke_core as kc


def is_emoji(ch: str) -> bool:
    """Rough test for emoji."""
    return ch in emoji.EMOJI_DATA


def twemoji_png_path(ch: str, out_dir: Path) -> Path:
    """Return cached Twemoji PNG path for this emoji codepoint."""
    codepoints = "-".join([f"{ord(c):x}" for c in ch])
    fn = f"twemoji_{codepoints}.png"
    return out_dir / "emoji_png" / fn


def download_twemoji_png(ch: str, out_dir: Path):
    """Download a Twemoji PNG for this emoji to assets."""
    import requests
    kc.ensure_dir(out_dir / "emoji_png")
    codepoints = "-".join([f"{ord(c):x}" for c in ch])
    fn = f"twemoji_{codepoints}.png"
    path = out_dir / "emoji_png" / fn
    if path.exists():
        return path
    url = f"https://github.com/twitter/twemoji/raw/master/assets/72x72/{codepoints}.png"
    r = requests.get(url, timeout=10)
    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)
    else:
        kc.warn(f"Missing Twemoji asset for {ch} ({url})")
    return path


def build_emoji_overlays(
    lines: List[str],
    starts: List[float],
    offset: float,
    canvas_w: int,
    canvas_h: int,
    font_px: int,
    out_dir: Path,
) -> Tuple[List[dict], List[str]]:
    """
    Build list of overlay specs and PNG input list.
    Each emoji becomes a separate overlay with scaling.
    """
    kc.ensure_dir(out_dir / "emoji_png")
    overlays = []
    png_inputs = []
    ends = []

    n = len(lines)
    for i in range(n):
        st = starts[i] + offset
        en = starts[i + 1] + offset - 0.15 if i < n - 1 else st + 3.0
        ends.append(en)

    scale_factor = font_px / 72.0
    line_height = int(font_px * 1.1)

    # pass 1: compute all overlays
    for i, text in enumerate(lines):
        if not any(is_emoji(ch) for ch in text):
            continue

        line_rows = text.split("\\N")
        total_height = len(line_rows) * line_height
        y0 = (canvas_h - total_height) // 2

        for row_idx, row in enumerate(line_rows):
            emojis_in_row = [ch for ch in row if is_emoji(ch)]
            if not emojis_in_row:
                continue
            row_w = int(len(emojis_in_row) * font_px)
            x0 = (canvas_w - row_w) // 2
            y = y0 + row_idx * line_height

            for col_idx, ch in enumerate(emojis_in_row):
                png = download_twemoji_png(ch, out_dir)
                if str(png) not in png_inputs:
                    png_inputs.append(str(png))
                idx = png_inputs.index(str(png)) + 1  # +1 because audio is 0
                x = x0 + int(col_idx * font_px)
                y_scaled = y
                overlays.append({
                    "png_stream_index": idx + 0,
                    "x": x,
                    "y": y_scaled,
                    "start": starts[i] + offset,
                    "end": ends[i],
                    "scale": scale_factor,
                })

    return overlays, png_inputs


def render_scaled_pngs(png_inputs: List[str], scale_factor: float, out_dir: Path) -> List[str]:
    """Pre-scale Twemoji PNGs if needed."""
    scaled_paths = []
    for src in png_inputs:
        src_path = Path(src)
        dst_path = out_dir / f"scaled_{src_path.name}"
        if dst_path.exists():
            scaled_paths.append(str(dst_path))
            continue
        img = Image.open(src_path).convert("RGBA")
        new_size = (int(img.width * scale_factor), int(img.height * scale_factor))
        img = img.resize(new_size, Image.LANCZOS)
        img.save(dst_path)
        scaled_paths.append(str(dst_path))
    return scaled_paths
# end of karaoke_emoji.py