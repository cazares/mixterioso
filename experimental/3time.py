"""
Step 3: Interactive lyric timestamping with curses.
"""

import os
import sys
import json
import curses
import time
import subprocess
from rich.console import Console

# === Config ===
TXT_DIR = "txts"
TIMING_DIR = "timing"
MP3_DIR = "mp3s"
FONT_SIZE = 120  # Not used here, but retained for consistency

console = Console()

def load_lyrics(slug):
    path = os.path.join(TXT_DIR, f"{slug}.txt")
    if not os.path.exists(path):
        console.print(f"[red]❌ Lyrics file not found at:[/] {path}")
        sys.exit(1)
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]

def save_timestamps(slug, timestamps):
    os.makedirs(TIMING_DIR, exist_ok=True)
    out_path = os.path.join(TIMING_DIR, f"{slug}.json")
    with open(out_path, "w") as f:
        json.dump(timestamps, f, indent=2)
    console.print(f"[green]✓ Saved timestamps to:[/] {out_path}")

def play_audio(slug):
    mp3_path = os.path.join(MP3_DIR, f"{slug}.mp3")
    if not os.path.exists(mp3_path):
        console.print(f"[red]❌ MP3 not found at:[/] {mp3_path}")
        sys.exit(1)
    subprocess.Popen(["afplay", mp3_path])

def run_curses_session(slug, stdscr):
    lyrics = load_lyrics(slug)
    timestamps = []
    start_time = time.time()

    stdscr.clear()
    stdscr.addstr(0, 0, f"Timing mode: press spacebar when each line is sung (q to quit)")
    stdscr.refresh()
    play_audio(slug)

    for idx, line in enumerate(lyrics):
        stdscr.clear()
        stdscr.addstr(2, 2, f"{idx+1}/{len(lyrics)}: {line}")
        stdscr.refresh()

        while True:
            key = stdscr.getkey()
            if key == " ":
                timestamp = round(time.time() - start_time, 2)
                timestamps.append({"line": line, "time": timestamp})
                break
            elif key == "q":
                return

    save_timestamps(slug, timestamps)

def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Usage:[/] python 3_time.py [slug]")
        sys.exit(1)
    slug = sys.argv[1]
    curses.wrapper(run_curses_session, slug)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(99)

# end of 3_time.py
