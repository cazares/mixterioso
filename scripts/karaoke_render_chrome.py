#!/usr/bin/env python3
"""
karaoke_render_chrome.py
Stable 1080p text+emoji renderer.
"""

import os
import sys
import argparse
import textwrap
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from PIL import Image
import time

def render_text_to_png(lines, output_dir, font_size):
    os.makedirs(output_dir, exist_ok=True)
    width, height = 1920, 1080

    html_template = """
    <html>
    <head>
    <meta charset="utf-8">
    <style>
      body {{
        margin: 0;
        padding: 0;
        width: {w}px;
        height: {h}px;
        display: flex;
        align-items: center;
        justify-content: center;
        background-color: black;
      }}
      .text {{
        color: white;
        font-size: {fs}px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        text-align: center;
        white-space: pre-wrap;
        line-height: 1.2;
        word-break: break-word;
      }}
    </style>
    </head>
    <body>
      <div class="text">{text}</div>
    </body>
    </html>
    """

    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--hide-scrollbars")
    chrome_options.add_argument(f"--window-size={width},{height}")
    chrome_options.binary_location = "/Applications/Chromium.app/Contents/MacOS/Chromium"

    driver = webdriver.Chrome(options=chrome_options)

    for i, raw_line in enumerate(lines):
        text = raw_line.replace("\\N", "\n").replace("\r", "")
        html = html_template.format(w=width, h=height, fs=font_size, text=text)
        tmp_file = f"/tmp/karaoke_render_{i}.html"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(html)

        driver.get("file://" + tmp_file)
        time.sleep(0.15)

        png_path = os.path.join(output_dir, f"frame_{i:04d}.png")
        driver.save_screenshot(png_path)

    driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Render static text+emoji slides to PNGs using Chrome.")
    parser.add_argument("--lyrics", required=True, help="Path to lyrics .txt file")
    parser.add_argument("--font-size", type=int, default=100, help="Font size (default: 100)")
    args = parser.parse_args()

    if not os.path.exists(args.lyrics):
        sys.exit(f"FATAL: lyrics file not found: {args.lyrics}")

    output_dir = "output/frames_chrome"
    os.makedirs(output_dir, exist_ok=True)

    with open(args.lyrics, "r", encoding="utf-8") as f:
        text = f.read().strip()

    lines = [x.strip() for x in text.splitlines() if x.strip()]

    print(f"🎬 Rendering {len(lines)} slides to {output_dir}/ ...")
    render_text_to_png(lines, output_dir, args.font_size)
    print("✅ Done rendering PNGs. Now run ffmpeg:")

    print(f"""
ffmpeg -y -framerate 1/1.5 \\
  -pattern_type glob \\
  -i "{output_dir}/*.png" \\
  -c:v libx264 -r 30 -pix_fmt yuv420p \\
  output/chrome_rendered_mp4s/{os.path.basename(args.lyrics).replace('.txt','')}_chrome_static.mp4
    """)


if __name__ == "__main__":
    main()

# end of karaoke_render_chrome.py
