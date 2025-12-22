#!/usr/bin/env python3
import sys
from pathlib import Path
import os

# Force real TTY for curses input
tty = open("/dev/tty")
os.dup2(tty.fileno(), sys.stdin.fileno())

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

"""
3_timing.py — Curses-based manual lyric timing tool (NO AI, CLI only)

Behavior:
- STRICT slug mode when --slug is provided (no slug prompts).
- Uses original MP3 only (never WAV/mix) for timing.
- Curses UI with color + bold where useful.
- Hotkeys (fixed step sizes):
    ENTER = stamp current lyric
    s     = skip current lyric
    e/r/t = rewind 1s / 3s / 5s
    d/f/g = forward 1s / 3s / 5s
    p     = pause / resume
    1–=   = insert music-note event at current time
    b     = insert BLANK event (single-space lyric) at current time
    q     = quit and save
- Rewind logic:
    When rewinding, any events (lyric timestamps, notes, blanks) AFTER
    the new playback position are removed. Current lyric index snaps to
    the last lyric whose timestamp is <= new time, or 0 if none.
- Output:
    timings/<slug>.csv with header: line_index,time_secs,text
    line_index is a simple 0..N-1 row index; text is lyric / notes / " ".
"""

import sys
import argparse
import curses
import time
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# ─────────────────────────────────────────────
# Bootstrap PATH
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    PATHS,
)

TXT_DIR = PATHS["txt"]
TIMINGS_DIR = PATHS["timings"]
MP3_DIR = PATHS["mp3"]

# ─────────────────────────────────────────────
# Args
# ─────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mixterioso manual timing (curses)")
    p.add_argument("--slug", help="Song slug (required in strict mode)")
    return p.parse_args()

# ─────────────────────────────────────────────
# Slug resolution
# ─────────────────────────────────────────────
def resolve_slug(args: argparse.Namespace) -> str:
    if args.slug:
        slug = args.slug.strip()
        if not slug:
            raise SystemExit("Invalid empty --slug.")
        log("SLUG", f"Using slug '{slug}' (strict mode)", GREEN)
        return slug

    # Legacy fallback if launched manually
    try:
        slug = input("Enter slug for timing: ").strip()
    except EOFError:
        raise SystemExit("Missing slug (EOF).")
    if not slug:
        raise SystemExit("Slug is required.")
    log("SLUG", f"Using slug '{slug}' (legacy prompt)", YELLOW)
    return slug

# ─────────────────────────────────────────────
# Lyrics
# ─────────────────────────────────────────────
def load_lyrics(slug: str) -> List[str]:
    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("TXT", f"Missing lyrics file: {txt_path}", RED)
        raise SystemExit(1)

    lines: List[str] = []
    for ln in txt_path.read_text(encoding="utf-8").splitlines():
        ln = ln.rstrip()
        if ln.strip():
            lines.append(ln)
    if not lines:
        log("TXT", "Lyrics appear empty.", YELLOW)
    return lines

# ─────────────────────────────────────────────
# Audio (MP3 only)
# ─────────────────────────────────────────────
def resolve_audio_path(slug: str) -> Path:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log("AUDIO", f"Missing MP3 for slug '{slug}' at {mp3_path}", RED)
        raise SystemExit(1)
    return mp3_path

# ─────────────────────────────────────────────
# AudioTransport
# ─────────────────────────────────────────────
class AudioTransport:
    """
    Small wrapper around ffplay/afplay.

    - Uses ffplay if available (with -ss seek), else afplay (no true seek).
    - Maintains a 'logical' position that is monotonic and seek-aware.
    """

    def __init__(self, audio_path: Path) -> None:
        self.audio_path = audio_path

        self._proc: Optional[subprocess.Popen] = None
        self._player_ffplay = shutil.which("ffplay")
        self._player_afplay = shutil.which("afplay")
        self._use_ffplay = self._player_ffplay is not None

        if not self._player_ffplay and not self._player_afplay:
            log("AUDIO", "Neither ffplay nor afplay found in PATH.", RED)
            raise SystemExit(1)

        if self._use_ffplay:
            log("AUDIO", f"Using ffplay (seek-capable): {self._player_ffplay}", CYAN)
        else:
            log("AUDIO", f"Using afplay (no seek): {self._player_afplay}", YELLOW)

        self._logical_pos = 0.0   # seconds into track
        self._state = "stopped"   # "stopped", "playing", "paused"
        self._state_time = 0.0    # monotonic time of last state change

    # Internal helpers
    def _kill_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _update_logical(self) -> None:
        now = time.monotonic()
        if self._state == "playing":
            delta = now - self._state_time
            if delta > 0:
                self._logical_pos += delta
        self._state_time = now

    def _launch_at(self, pos: float) -> None:
        self._kill_proc()
        self._logical_pos = max(0.0, pos)
        self._state = "playing"
        self._state_time = time.monotonic()

        if self._use_ffplay:
            cmd = [
                self._player_ffplay,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "quiet",
                "-ss",
                f"{self._logical_pos:.3f}",
                str(self.audio_path),
            ]
        else:
            # afplay cannot seek; restart from 0 but keep logical position for timestamps
            if self._logical_pos > 0.0:
                log("AUDIO", "afplay cannot seek; audio restarts but logical time is respected.", YELLOW)
            cmd = [self._player_afplay, str(self.audio_path)]

        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as e:
            log("AUDIO", f"Failed to start audio: {e}", RED)
            raise SystemExit(1)

    # Public API
    def start(self) -> None:
        self._launch_at(0.0)

    def current_time(self) -> float:
        self._update_logical()
        return max(0.0, self._logical_pos)

    def seek_relative(self, delta: float) -> float:
        """
        Move logical position by delta seconds (positive or negative).
        Returns new logical position.
        """
        target = max(0.0, self.current_time() + delta)
        self._launch_at(target)
        return self.current_time()

    def toggle_pause(self) -> None:
        if self._state == "playing":
            self._update_logical()
            self._kill_proc()
            self._state = "paused"
        elif self._state == "paused":
            self._launch_at(self._logical_pos)
        else:
            self._launch_at(self._logical_pos)

    def stop(self) -> None:
        self._kill_proc()
        self._update_logical()
        self._state = "stopped"

# ─────────────────────────────────────────────
# Notes
# ─────────────────────────────────────────────
NOTE_KEY_MAP: Dict[str, str] = {
    "1": "♪",
    "2": "♫",
    "3": "♬",
    "4": "♩",
    "5": "♪♫",
    "6": "♫♬",
    "7": "♬♩",
    "8": "♪♩",
    "9": "♪♬",
    "0": "♫♪",
    "-": "♩♬♪",
    "=": "♫♪♬♩",
}

# ─────────────────────────────────────────────
# Curses UI helpers
# ─────────────────────────────────────────────
def draw_intro_box(stdscr, slug: str, COLOR_HDR, COLOR_GRAY, COLOR_HOT) -> None:
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    title = f"Mixterioso Timing – {slug}"
    try:
        stdscr.addstr(0, 0, title[: max(0, w - 1)], COLOR_HDR)
    except curses.error:
        pass

    # Box content lines (label, description)
    lines: List[Tuple[str, str]] = [
        ("ENTER", "stamp current lyric"),
        ("s", "skip current lyric"),
        ("e/r/t", "rewind 1s / 3s / 5s"),
        ("d/f/g", "forward 1s / 3s / 5s"),
        ("p", "pause / resume audio"),
        ("1–=", "insert note event"),
        ("b", "insert BLANK event (\" \")"),
        (">", "NO LYRICS: keep title card only"),
        ("q", "quit and save CSV"),
    ]

    max_label = max(len(label) for label, _ in lines)
    max_desc = max(len(desc) for _, desc in lines)

    inner_width = max_label + 3 + max_desc  # label + " – " + desc
    box_width = inner_width + 4  # padding
    box_width = min(box_width, max(20, w - 2))

    # Center box horizontally
    start_x = max(0, (w - box_width) // 2)
    start_y = 2

    # Draw box with Unicode borders
    top = "┌" + "─" * (box_width - 2) + "┐"
    mid = "├" + "─" * (box_width - 2) + "┤"
    bot = "└" + "─" * (box_width - 2) + "┘"

    try:
        stdscr.addstr(start_y, start_x, top, COLOR_GRAY)
        stdscr.addstr(start_y + 1, start_x, "│" + " " * (box_width - 2) + "│", COLOR_GRAY)
        header = "Controls"
        header_x = start_x + (box_width - len(header)) // 2
        stdscr.addstr(start_y + 1, header_x, header, COLOR_HDR)
        stdscr.addstr(start_y + 2, start_x, mid, COLOR_GRAY)
    except curses.error:
        pass

    row = start_y + 3
    for label, desc in lines:
        if row >= h - 2:
            break
        line = "│ " + label.ljust(max_label) + " – " + desc.ljust(max_desc) + " │"
        try:
            stdscr.addstr(row, start_x, "│ ", COLOR_GRAY)
            stdscr.addstr(row, start_x + 2, label.ljust(max_label), COLOR_HOT | curses.A_BOLD)
            stdscr.addstr(row, start_x + 2 + max_label, " – ", COLOR_GRAY)
            stdscr.addstr(row, start_x + 5 + max_label, desc.ljust(max_desc), COLOR_GRAY)
            stdscr.addstr(row, start_x + box_width - 1, "│", COLOR_GRAY)
        except curses.error:
            pass
        row += 1

    try:
        stdscr.addstr(row, start_x, bot, COLOR_GRAY)
    except curses.error:
        pass

    row += 2
    if row < h:
        msg = "Press ENTER to start audio + timing."
        try:
            stdscr.addstr(row, max(0, (w - len(msg)) // 2), msg, COLOR_HOT | curses.A_BOLD)
        except curses.error:
            pass

    stdscr.refresh()

def draw_command_bar(stdscr, COLOR_HOT, COLOR_GRAY) -> None:
    h, w = stdscr.getmaxyx()
    # Row 2 and 3 for command groups
    row1 = 2
    row2 = 3

    def draw_segments(row: int, segments: List[Tuple[str, bool]]) -> None:
        col = 0
        for text, is_hot in segments:
            if col >= w - 1:
                break
            chunk = text[: max(0, w - 1 - col)]
            color = COLOR_HOT | curses.A_BOLD if is_hot else COLOR_GRAY
            try:
                stdscr.addstr(row, col, chunk, color)
            except curses.error:
                pass
            col += len(chunk)

    segments1: List[Tuple[str, bool]] = [
        ("[ENTER]", True), (" stamp   ", False),
        ("[s]", True),     (" skip   ", False),
        ("[e/r/t]", True), (" rewind   ", False),
        ("[d/f/g]", True), (" forward", False),
    ]

    segments2: List[Tuple[str, bool]] = [
        ("[p]", True),     (" pause  ", False),
        ("[1–=]", True),   (" notes  ", False),
        ("[b]", True),     (" blank  ", False),
        ("[>]", True),     (" no-lyrics  ", False),
        ("[q]", True),     (" quit", False),
    ]

    draw_segments(row1, segments1)
    draw_segments(row2, segments2)

# ─────────────────────────────────────────────
# Curses UI
# ─────────────────────────────────────────────
def curses_main(stdscr, slug: str, lyrics: List[str], audio_path: Path) -> None:
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)   # primary lyric text
    curses.init_pair(2, curses.COLOR_CYAN,   -1)   # headers
    curses.init_pair(3, curses.COLOR_MAGENTA, -1)  # hotkeys / accents
    curses.init_pair(4, curses.COLOR_RED,    -1)   # errors
    curses.init_pair(5, curses.COLOR_WHITE,  -1)   # gray (dim)

    COLOR_MAIN = curses.color_pair(1)
    COLOR_HDR  = curses.color_pair(2) | curses.A_BOLD
    COLOR_HOT  = curses.color_pair(3)
    COLOR_ERR  = curses.color_pair(4) | curses.A_BOLD
    COLOR_GRAY = curses.color_pair(5) | curses.A_DIM

    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TIMINGS_DIR / f"{slug}.csv"

    # Lyric events: map lyric line index -> timestamp
    lyric_times: Dict[int, float] = {}

    # Extra events: notes + blanks [(time, text)]
    extra_events: List[Tuple[float, str]] = []

    # Mini console event log: (is_error, ts_str, message)
    event_log: List[Tuple[bool, str, str]] = []

    def log_event(msg: str, is_error: bool = False) -> None:
        ts_raw = time.strftime("%H:%M:%S")
        event_log.append((is_error, ts_raw, msg))
        if len(event_log) > 8:
            del event_log[0]

    transport = AudioTransport(audio_path)

    # Intro screen with controls box
    draw_intro_box(stdscr, slug, COLOR_HDR, COLOR_GRAY, COLOR_HOT)

    # Wait for ENTER to start
    while True:
        ch = stdscr.getch()
        if ch in (10, 13):
            break

    transport.start()
    log_event("Timing started, audio playback running.")

    current_idx = 0
    num_lines = len(lyrics)

    def handle_seek(delta_seconds: float) -> None:
        nonlocal current_idx, lyric_times, extra_events
        before = transport.current_time()
        after = transport.seek_relative(delta_seconds)

        if delta_seconds < 0:
            # Rewind: remove events beyond new time
            threshold = after
            removed_lyrics = sorted(
                [idx for idx, ts in lyric_times.items() if ts > threshold]
            )
            removed_extras = [(ts, txt) for (ts, txt) in extra_events if ts > threshold]

            if removed_lyrics or removed_extras:
                for idx in removed_lyrics:
                    ts = lyric_times[idx]
                    log_event(f"Removed lyric line {idx} at {ts:.3f}s due to rewind.")
                    del lyric_times[idx]
                if removed_extras:
                    for ts, txt in removed_extras:
                        log_event(f"Removed extra event '{txt}' at {ts:.3f}s due to rewind.")
                    extra_events = [(ts, txt) for (ts, txt) in extra_events if ts <= threshold]

                # Recompute current_idx: last lyric whose time <= new time, +1
                if lyric_times:
                    latest_idx = max(
                        (i for i, ts in lyric_times.items() if ts <= threshold),
                        default=-1,
                    )
                    current_idx = max(0, latest_idx + 1)
                else:
                    current_idx = 0
            log_event(f"Rewind {before:.3f}s → {after:.3f}s")
        else:
            log_event(f"Forward {before:.3f}s → {after:.3f}s")

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        now_t = transport.current_time()

        # Header
        try:
            stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}"[: max(0, w - 1)], COLOR_HDR)
            stdscr.addstr(1, 0, f"Time: {now_t:7.2f}s"[: max(0, w - 1)], COLOR_MAIN)
        except curses.error:
            pass

        # Command bar (rows 2–3)
        draw_command_bar(stdscr, COLOR_HOT, COLOR_GRAY)

        # Lyrics window
        base_row = 5
        log_rows = 9
        window_size = max(3, h - base_row - log_rows - 1)
        offset = max(0, current_idx - window_size // 2)

        for i in range(window_size):
            idx = offset + i
            if idx >= num_lines:
                break
            line = lyrics[idx]
            prefix = f"{idx:3d}: "
            row = base_row + i

            if row >= h:
                break

            if idx == current_idx:
                text = (prefix + line)[: max(0, w - 1)]
                try:
                    stdscr.addstr(row, 0, text, COLOR_MAIN | curses.A_REVERSE | curses.A_BOLD)
                except curses.error:
                    pass
            else:
                try:
                    # Line number (gray)
                    stdscr.addstr(row, 0, prefix[: max(0, w - 1)], COLOR_GRAY)
                    # Lyric text (yellow)
                    if len(prefix) < w - 1:
                        stdscr.addstr(
                            row,
                            len(prefix),
                            line[: max(0, w - 1 - len(prefix))],
                            COLOR_MAIN,
                        )
                except curses.error:
                    pass

        # Mini console feed at bottom
        log_start = base_row + window_size + 1
        if log_start < h:
            try:
                stdscr.addstr(log_start, 0, "-" * max(0, w - 1), COLOR_GRAY)
            except curses.error:
                pass
            visible_logs = event_log[-(h - log_start - 1) :]
            for i, (is_error, ts_str, msg) in enumerate(visible_logs, start=1):
                row = log_start + i
                if row >= h:
                    break
                base_color = COLOR_ERR if is_error else COLOR_GRAY
                col = 0
                try:
                    label = "[ERR " if is_error else "[LOG "
                    if col < w - 1:
                        chunk = label[: max(0, w - 1 - col)]
                        stdscr.addstr(row, col, chunk, base_color)
                    col += len(label)

                    if col < w - 1:
                        chunk = ts_str[: max(0, w - 1 - col)]
                        stdscr.addstr(row, col, chunk, base_color | curses.A_BOLD)
                    col += len(ts_str)

                    if col < w - 1:
                        stdscr.addstr(row, col, "] ", base_color)
                    col += 2

                    if col < w - 1:
                        stdscr.addstr(
                            row,
                            col,
                            msg[: max(0, w - 1 - col)],
                            base_color,
                        )
                except curses.error:
                    pass

        stdscr.refresh()

        # Input
        ch = stdscr.getch()

        # Quit
        if ch in (ord("q"), ord("Q")):
            log_event("Quit requested; saving CSV and exiting.")
            break

        # Pause / resume
        if ch in (ord("p"), ord("P")):
            transport.toggle_pause()
            log_event(f"Toggle pause/resume at {transport.current_time():.3f}s")
            continue

        # Seek hotkeys
        if ch in (ord("e"), ord("E")):
            handle_seek(-1.0)
            continue
        if ch in (ord("r"), ord("R")):
            handle_seek(-3.0)
            continue
        if ch in (ord("t"), ord("T")):
            handle_seek(-5.0)
            continue
        if ch in (ord("d"), ord("D")):
            handle_seek(+1.0)
            continue
        if ch in (ord("f"), ord("F")):
            handle_seek(+3.0)
            continue
        if ch in (ord("g"), ord("G")):
            handle_seek(+5.0)
            continue

        # Skip lyric (no timestamp recorded)
        if ch in (ord("s"), ord("S")):
            if current_idx < num_lines:
                log_event(f"Skipped lyric line {current_idx}: {lyrics[current_idx][:40]!r}")
                current_idx = min(num_lines, current_idx + 1)
            else:
                log_event("Skip requested but no more lyrics.")
            continue

        # Character-based commands
        if 0 <= ch <= 255:
            ch_char = chr(ch)
        else:
            ch_char = ""

        # Note insertion
        if ch_char in NOTE_KEY_MAP:
            note_txt = NOTE_KEY_MAP[ch_char]
            ts = transport.current_time()
            extra_events.append((ts, note_txt))
            log_event(f"Note inserted '{note_txt}' at {ts:.3f}s")
            continue

        # Blank insertion (single space lyric)
        if ch_char in ("b", "B"):
            ts = transport.current_time()
            extra_events.append((ts, " "))
            log_event(f"Blank event inserted at {ts:.3f}s")
            continue

                # --- NEW FEATURE: NO-LYRICS HOTKEY ------------------------------
        if ch_char == ">":
            # Pause audio so they don’t miss anything
            transport.toggle_pause()

            # Leave curses mode to safely prompt in the terminal
            curses.endwin()
            print()
            print("You pressed '>': This will produce a NO-LYRICS render.")
            print("To confirm, type exactly:  no lyrics")
            try:
                confirm = input("Confirm: ").strip().lower()
            except EOFError:
                confirm = ""

            if confirm == "no lyrics":
                # Write an EMPTY CSV with only the header row.
                out_path = TIMINGS_DIR / f"{slug}.csv"
                with out_path.open("w", encoding="utf-8") as f:
                    f.write("line_index,time_secs,text\n")
                log("CSV", f"Wrote EMPTY no-lyrics CSV: {out_path}", GREEN)
                return  # fully exit curses_main

            else:
                print("No-lyrics mode cancelled. Returning to timing UI...")
                time.sleep(1)
                # Resume curses
                stdscr = curses.initscr()
                curses.noecho()
                curses.cbreak()
                stdscr.keypad(True)
                transport.toggle_pause()
                continue
        # -----------------------------------------------------------------

        # ENTER = stamp current lyric
        if ch in (10, 13):
            if current_idx < num_lines:
                ts = transport.current_time()
                lyric_times[current_idx] = ts
                log_event(f"Lyric line {current_idx} stamped at {ts:.3f}s")
                current_idx += 1
            else:
                log_event("ENTER pressed but no more lyrics to stamp.")
            continue

        # All other keys ignored

    transport.stop()

    # Merge lyric and extra events, then sort by time
    merged: List[Tuple[float, str]] = []
    for idx, ts in lyric_times.items():
        text = lyrics[idx]
        merged.append((ts, text))
    for ts, text in extra_events:
        merged.append((ts, text))

    merged_sorted = sorted(merged, key=lambda x: x[0])

    # Write CSV
    with out_path.open("w", encoding="utf-8") as f:
        f.write("line_index,time_secs,text\n")
        for idx, (ts, text) in enumerate(merged_sorted):
            f.write(f"{idx},{ts:.6f},{text}\n")

    log("CSV", f"Wrote: {out_path}", GREEN)

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main() -> None:
    log("MODE", "Manual timing (curses, strict --slug mode)", CYAN)
    args = parse_args()
    slug = resolve_slug(args)
    lyrics = load_lyrics(slug)
    audio_path = resolve_audio_path(slug)

    try:
        curses.wrapper(curses_main, slug, lyrics, audio_path)
        # If curses_main returns normally (including NO-LYRICS early exit),
        # we simply stop here.
        return
    except KeyboardInterrupt:
        log("ABORT", "Interrupted by user (Ctrl+C).", YELLOW)

if __name__ == "__main__":
    main()

# end of 3_timing.py