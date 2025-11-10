"""
Step 5: Generate final MP4 with hardcoded lyrics and audio.
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from rich.console import Console

# === Config ===
TIMING_DIR = "timing"
OFFSET_DIR = "offsets"
MP3_DIR = "mp3s"
OUTPUT_DIR = "mp4s"
FONT_SIZE = 120
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SAFE_REGEN = True  # skip rendering if output exists and is recent

console = Console()

def load_json_or_die(path):
    if not os.path.exists(path):
        console.print(f"[red]❌ Missing required file:[/] {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)

def build_ffmpeg_command(slug, audio_path, timing_data, offset):
    drawtext_filters = []
    for item in timing_data:
        ts = item["time"] + offset
        line = item["line"].replace("'", r"\'").replace(":", r"\:")
        drawtext = (
            f"drawtext=fontfile='{FONT_PATH}':text='{line}':"
            f"fontsize={FONT_SIZE}:fontcolor=white:x=(w-text_w)/2:y=h-160:"
            f"enable='between(t,{ts:.2f},{ts+2:.2f})'"
        )
        drawtext_filters.append(drawtext)
    filter_chain = ",".join(drawtext_filters)
    out_path = os.path.join(OUTPUT_DIR, f"{slug}.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=size=1280x720:rate=24:color=black",
        "-i", audio_path,
        "-filter_complex", filter_chain,
        "-shortest",
        "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac",
        out_path
    ]
    return cmd, out_path

def should_skip(output_path):
    return os.path.exists(output_path) and SAFE_REGEN

def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Usage:[/] python 5_gen_mp4.py [slug]")
        sys.exit(1)
    slug = sys.argv[1]

    timing_path = os.path.join(TIMING_DIR, f"{slug}.json")
    offset_path = os.path.join(OFFSET_DIR, f"{slug}.json")
    audio_path = os.path.join(MP3_DIR, f"{slug}.mp3")

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    timing_data = load_json_or_die(timing_path)
    offset = 0.0
    if os.path.exists(offset_path):
        offset_data = load_json_or_die(offset_path)
        offset = float(offset_data.get("offset", 0.0))
        console.print(f"[blue]Applying offset of:[/] {offset:+.2f}s")

    ffmpeg_cmd, out_path = build_ffmpeg_command(slug, audio_path, timing_data, offset)

    if should_skip(out_path):
        console.print(f"[yellow]⚠️  Skipping generation, output exists:[/] {out_path}")
        return

    console.print(f"[cyan]🎥 Generating final karaoke MP4...[/cyan]")
    try:
        subprocess.run(ffmpeg_cmd, check=True)
        console.print(f"[bold green]✓ Final video saved to:[/] {out_path}")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]❌ ffmpeg failed with code {e.returncode}[/red]")
        sys.exit(e.returncode)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(99)

# end of 5_gen_mp4.py
