"""
Step 4: Calibrate A/V offset interactively using a curses UI.
"""

import os
import sys
import json
import subprocess
import time
import curses
from rich.console import Console

# === Config ===
MP4_DIR = "mp4s"
TIMING_DIR = "timing"
OFFSET_DIR = "offsets"
DEFAULT_WINDOW = (30, 60)  # seconds
FPS = 24  # affects subtitle positioning
SLUG_REQUIRED = True

console = Console()

def load_timing(slug):
    path = os.path.join(TIMING_DIR, f"{slug}.json")
    if not os.path.exists(path):
        console.print(f"[red]❌ No timing data found at:[/] {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)

def load_mp4_path(slug):
    path = os.path.join(MP4_DIR, f"{slug}.mp4")
    if not os.path.exists(path):
        console.print(f"[red]❌ No mp4 found at:[/] {path}")
        sys.exit(1)
    return path

def play_snippet(mp4_path, start, end):
    cmd = [
        "ffplay", "-autoexit", "-nodisp",
        "-ss", str(start),
        "-t", str(end - start),
        mp4_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def get_lines_in_window(timing, start, end, offset):
    return [
        t["line"]
        for t in timing
        if start <= t["time"] + offset <= end
    ]

def run_calibration_ui(stdscr, slug, mp4_path, timing, start, end):
    offset = 0.0
    finalized = False

    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(-1)

    while not finalized:
        stdscr.clear()
        stdscr.addstr(1, 2, f"[Calibration] Adjust A/V Offset for: {slug}")
        stdscr.addstr(2, 2, f"Current Offset: {offset:+.2f} sec")
        stdscr.addstr(3, 2, "→ Arrow Left/Right: -/+ 0.1s")
        stdscr.addstr(4, 2, "→ Arrow Down/Up:    -/+ 0.5s")
        stdscr.addstr(5, 2, "[space] Play | [s] Save | [q] Quit")

        lines = get_lines_in_window(timing, start, end, offset)
        for i, line in enumerate(lines[:5], 7):
            stdscr.addstr(i, 4, line)

        stdscr.refresh()
        key = stdscr.getkey()

        if key in ("KEY_LEFT",):
            offset -= 0.1
        elif key in ("KEY_RIGHT",):
            offset += 0.1
        elif key in ("KEY_DOWN",):
            offset -= 0.5
        elif key in ("KEY_UP",):
            offset += 0.5
        elif key == " ":
            stdscr.clear()
            stdscr.addstr(2, 2, f"Playing mp4 with offset {offset:+.2f}s...")
            stdscr.refresh()
            play_snippet(mp4_path, start, end)
        elif key in ("s", "S"):
            out_path = os.path.join(OFFSET_DIR, f"{slug}.json")
            os.makedirs(OFFSET_DIR, exist_ok=True)
            with open(out_path, "w") as f:
                json.dump({"offset": offset}, f, indent=2)
            stdscr.addstr(15, 2, f"Saved offset to {out_path}")
            stdscr.refresh()
            time.sleep(1.5)
            finalized = True
        elif key in ("q", "Q"):
            break

def main():
    if len(sys.argv) < 2 and SLUG_REQUIRED:
        console.print("[red]❌ Please provide a slug to calibrate.[/red]")
        sys.exit(1)

    slug = sys.argv[1]
    start, end = DEFAULT_WINDOW

    if len(sys.argv) >= 4:
        start = int(sys.argv[2])
        end = int(sys.argv[3])

    mp4_path = load_mp4_path(slug)
    timing = load_timing(slug)
    curses.wrapper(run_calibration_ui, slug, mp4_path, timing, start, end)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled by user.[/yellow]")
        sys.exit(99)

# end of 4_calibrate.py
