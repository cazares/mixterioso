#!/usr/bin/env python3
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
    # macOS-friendly; change to ffplay if you prefer
    subprocess.Popen(["afplay", mp3_path])


def run_curses_session(stdscr, slug):
    """Main curses loop. NOTE: stdscr is first, slug second (required by curses.wrapper)."""
    lyrics = load_lyrics(slug)
    timestamps = []

    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.clear()
    stdscr.addstr(0, 0, f"Timing mode for slug: {slug}")
    stdscr.addstr(1, 0, "Press [SPACE] when each line is sung. Press 'q' to quit.")
    stdscr.addstr(3, 0, "Press any key to start playback...")
    stdscr.refresh()
    stdscr.getkey()

    play_audio(slug)
    start_time = time.time()

    for idx, line in enumerate(lyrics):
        stdscr.clear()
        stdscr.addstr(0, 0, f"Line {idx+1}/{len(lyrics)}")
        stdscr.addstr(2, 2, line)
        stdscr.addstr(4, 0, "Press [SPACE] at the correct moment, or 'q' to abort.")
        stdscr.refresh()

        while True:
            key = stdscr.getkey()
            if key == " ":
                timestamp = round(time.time() - start_time, 2)
                timestamps.append({"line": line, "time": timestamp})
                break
            elif key.lower() == "q":
                # abort timing and do not save partial results
                return

    save_timestamps(slug, timestamps)


def main():
    if len(sys.argv) < 2:
        console.print("[red]❌ Usage:[/] python 3time.py [slug]")
        sys.exit(1)
    slug = sys.argv[1].strip()
    curses.wrapper(run_curses_session, slug)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(99)

# end of 3time.py
