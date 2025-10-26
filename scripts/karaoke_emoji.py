#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_emoji.py – render emoji overlays as PNGs and feed ffmpeg overlay chain.

Key properties:
- Guaranteed color emoji using Twemoji PNGs.
- Each unique PNG emoji is only added to ffmpeg once.
  We then reuse its same input index for all overlay windows.
- Overlay specs are returned for karaoke_audio_video.py to build filter_complex.

Output contract:
build_emoji_overlays(...) returns:
  filter_cmds: [
     { "png_stream_index": <int>, "x": <int>, "y": <int>,
       "start": <float>, "end": <float> },
     ...
  ]
  extra_inputs: [ "/abs/or/rel/path/to/emoji1.png",
                  "/abs/or/rel/path/to/emoji2.png",
                  ... ]
The caller:
  - does `-i` for audio first
  - then `-i` for each path in extra_inputs (in given order)
  That means:
    audio is input #0
    first PNG in extra_inputs is input #1
    second PNG is input #2
    etc.
So for ffmpeg overlay we will reference `[1:v]`, `[2:v]`, `[3:v]`, etc.

IMPORTANT:
karaoke_audio_video.py assumed input_idx_start = 2.
We will change that assumption here. We'll lock it to 1 so math is simpler.
Then karaoke_audio_video.py must stop assuming "2" and just trust png_stream_index.
We'll handle that there after this.
"""

from pathlib import Path
from typing import List, Tuple, Dict
from PIL import ImageFont, Image, ImageDraw
import urllib.request, re
import karaoke_core as kc


def fetch_twemoji_png(char: str, out_dir: Path) -> Path:
    """
    Download a Twemoji PNG (72x72) for a single emoji codepoint sequence.
    Cache it in out_dir/twemoji_<codepoints>.png
    """
    codepoints = "-".join([f"{ord(c):x}" for c in char])
    out_dir.mkdir(parents=True, exist_ok=True)
    local_path = out_dir / f"twemoji_{codepoints}.png"
    if not local_path.exists():
        url = f"https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/{codepoints}.png"
        try:
            with urllib.request.urlopen(url) as r:
                local_path.write_bytes(r.read())
            kc.info(f"🧩 Downloaded Twemoji {char} ({codepoints})")
        except Exception:
            kc.warn(f"⚠️ Could not fetch Twemoji for {char} ({url})")
    return local_path


def line_needs_color_render(line: str) -> bool:
    """
    Heuristic: if line has any non-basic-ASCII char we consider it emoji/needs overlay.
    """
    for ch in line:
        code = ord(ch)
        if code < 0x20:
            continue
        if 0x20 <= code <= 0x7E:
            continue
        return True
    return False


def build_emoji_overlays(
    lines: List[str],
    starts: List[float],
    offset: float,
    canvas_w: int,
    canvas_h: int,
    font_px: int,
    out_dir: Path,
) -> Tuple[List[Dict], List[str]]:
    """
    Strategy now:
    - We IGNORE font rendering. We only do Twemoji PNGs.
    - We gather all emojis per line (regex range 1F300-1FAFF).
    - Each unique emoji PNG path is added once to extra_inputs.
    - We reuse its assigned index for all overlay times.

    We return:
      filter_cmds: list of dicts with {png_stream_index,x,y,start,end}
      extra_inputs: ordered PNG paths corresponding to stream indexes

    ffmpeg input layout we guarantee:
      0 = audio (handled by caller)
      1 = first PNG in extra_inputs
      2 = second PNG in extra_inputs
      ...
    So png_stream_index is 1-based for PNGs.
    """

    kc.ensure_dir(out_dir / "emoji_png")

    # compute line end times like in karaoke_core.write_ass
    ends = []
    n = len(lines)
    for i in range(n):
        st = starts[i] + offset
        if i < n - 1:
            en = starts[i + 1] + offset - 0.15
            if en <= st:
                en = st + 0.15
        else:
            en = st + 3.0
        ends.append(en)

    # We will build:
    #   extra_inputs = [png_path_0, png_path_1, ...]
    # and a lookup:
    #   png_path -> stream_index_for_ffmpeg
    # where stream_index_for_ffmpeg is 1-based because audio is 0.
    extra_inputs: List[str] = []
    png_index_map: Dict[str, int] = {}

    filter_cmds: List[Dict] = []

    kc.info("🧩 Using Twemoji PNG overlay mode")

    for i, text in enumerate(lines):
        if not line_needs_color_render(text):
            continue

        st = starts[i] + offset
        en = ends[i]

        # pull emojis in this line
        emojis = re.findall(r'[\U0001F300-\U0001FAFF]', text)
        if not emojis:
            continue

        # We'll drop all emojis for this line at a single position on screen.
        # Simple centering guess. You can tune this if you want nicer layout.
        x = int(canvas_w * 0.45)
        y = int(canvas_h * 0.4)

        for e in emojis:
            png_path_obj = fetch_twemoji_png(e, out_dir / "emoji_png")
            if not png_path_obj.exists():
                continue

            png_path = str(png_path_obj)

            # assign stable index for this PNG if first time
            if png_path not in png_index_map:
                extra_inputs.append(png_path)
                # index is position in extra_inputs + 1 because audio is #0
                png_index_map[png_path] = len(extra_inputs)

            stream_idx = png_index_map[png_path]  # 1-based

            filter_cmds.append({
                "png_stream_index": stream_idx,
                "x": x,
                "y": y,
                "start": st,
                "end": en,
            })

    # Done. No out-of-range index possible because:
    # max png_stream_index == len(extra_inputs)
    # Caller will pass exactly len(extra_inputs) PNGs after audio.
    return filter_cmds, extra_inputs

# end of karaoke_emoji.py
