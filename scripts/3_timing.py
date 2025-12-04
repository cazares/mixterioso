#!/usr/bin/env python3
"""
3_timing.py — Curses-based manual lyric timing tool (NO AI, CLI only)

Behavior:
- STRICT slug mode when --slug is provided (no slug prompts).
- Uses original MP3 only (never WAV/mix) for timing.
- Curses UI with color + bold where useful.
- Hotkeys (no adjustable step):
    e = rewind 1s
    r = rewind 3s
    t = rewind 5s
    d = forward 1s
    f = forward 3s
    g = forward 5s
    p = pause / resume
    ENTER = stamp current lyric line at current time
    s = skip current lyric line
    1–= = insert music note “lyric” at current time
    b = insert blank “lyric” (text = " ") at current time
    q = quit and save
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

    lines = []
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
                "-loglevel", "quiet",
                "-ss", f"{self._logical_pos:.3f}",
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
NOTE_KEY_MAP = {
    "1": "♪",       "2": "♫",       "3": "♬",       "4": "♩",
    "5": "♪♫",      "6": "♫♬",      "7": "♬♩",      "8": "♪♩",
    "9": "♪♬",      "0": "♫♪",      "-": "♩♬♪",     "=": "♫♪♬♩",
}

# ─────────────────────────────────────────────
# Curses UI
# ─────────────────────────────────────────────
def curses_main(stdscr, slug: str, lyrics: List[str], audio_path: Path) -> None:
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)   # primary text
    curses.init_pair(2, curses.COLOR_CYAN,   -1)   # header
    curses.init_pair(3, curses.COLOR_MAGENTA, -1)  # controls
    curses.init_pair(4, curses.COLOR_RED,    -1)   # warnings

    COLOR_MAIN = curses.color_pair(1)
    COLOR_HDR  = curses.color_pair(2) | curses.A_BOLD
    COLOR_CTRL = curses.color_pair(3)
    COLOR_ERR  = curses.color_pair(4) | curses.A_BOLD

    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TIMINGS_DIR / f"{slug}.csv"

    # Lyric events: map lyric line index -> timestamp
    lyric_times: Dict[int, float] = {}

    # Extra events: notes + blanks [(time, text)]
    extra_events: List[Tuple[float, str]] = []

    # Mini console event log
    event_log: List[str] = []

    def log_event(msg: str, is_error: bool = False) -> None:
        prefix = "ERR" if is_error else "LOG"
        ts = time.strftime("%H:%M:%S")
        event_log.append(f"[{prefix} {ts}] {msg}")
        if len(event_log) > 8:
            del event_log[0]

    transport = AudioTransport(audio_path)

    # Pre-start splash
    stdscr.clear()
    try:
        stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}", COLOR_HDR)
        stdscr.addstr(2, 0, "Controls:", COLOR_CTRL | curses.A_BOLD)
        stdscr.addstr(3, 2, "ENTER     = stamp current lyric", COLOR_CTRL)
        stdscr.addstr(4, 2, "s         = skip current lyric", COLOR_CTRL)
        stdscr.addstr(5, 2, "e/r/t     = rewind 1s / 3s / 5s", COLOR_CTRL)
        stdscr.addstr(6, 2, "d/f/g     = forward 1s / 3s / 5s", COLOR_CTRL)
        stdscr.addstr(7, 2, "p         = pause / resume", COLOR_CTRL)
        stdscr.addstr(8, 2, "1–=       = insert note event", COLOR_CTRL)
        stdscr.addstr(9, 2, "b         = insert BLANK event (\" \")", COLOR_CTRL)
        stdscr.addstr(10,2, "q         = quit and save CSV", COLOR_CTRL)
        stdscr.addstr(12,0, "Press ENTER to start audio + timing.", COLOR_MAIN | curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()

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
            stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}", COLOR_HDR)
            stdscr.addstr(1, 0, f"Time: {now_t:7.2f}s", COLOR_MAIN)
            stdscr.addstr(
                2,
                0,
                "[ENTER] stamp  [s] skip  [e/r/t] ←  [d/f/g] →  [p] pause  [1–=] notes  [b] blank  [q] quit",
                COLOR_CTRL,
            )
        except curses.error:
            pass

        # Lyrics window
        base_row = 4
        log_rows = 9
        window_size = max(3, h - base_row - log_rows - 1)
        offset = max(0, current_idx - window_size // 2)

        for i in range(window_size):
            idx = offset + i
            if idx >= num_lines:
                break
            line = lyrics[idx]
            prefix = f"{idx:3d}: "
            text = (prefix + line)[: max(0, w - 1)]
            row = base_row + i
            try:
                if idx == current_idx:
                    stdscr.addstr(row, 0, text, COLOR_MAIN | curses.A_REVERSE | curses.A_BOLD)
                else:
                    stdscr.addstr(row, 0, text, COLOR_MAIN)
            except curses.error:
                pass

        # Mini console feed at bottom
        log_start = base_row + window_size + 1
        if log_start < h:
            try:
                stdscr.addstr(log_start, 0, "-" * max(0, w - 1), COLOR_MAIN)
            except curses.error:
                pass
            visible_logs = event_log[-(h - log_start - 1) :]
            for i, msg in enumerate(visible_logs, start=1):
                row = log_start + i
                if row >= h:
                    break
                try:
                    stdscr.addstr(row, 0, msg[: max(0, w - 1)], COLOR_MAIN)
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
    except KeyboardInterrupt:
        log("ABORT", "Interrupted by user (Ctrl+C).", YELLOW)

if __name__ == "__main__":
    main()

# end of 3_timing.py
