#!/usr/bin/env python3
"""
3_timing.py — Curses-based manual lyric timing tool (NO AI)

Key behaviors:
- STRICT slug mode: if --slug is provided, never prompt for slug.
- AUTO playback: plays the original MP3 for the slug (never WAV).
- Global clock: timestamps are song-time seconds, with seek-aware transport.
- Rewind: 'r' rewinds by N seconds (default 5s; adjust with +/-).
- Fast-forward: 'f' jumps forward by N seconds.
- Pause/Resume: 'p' toggles pause for both timing and audio.
- Note insertion: keys 1–= insert musical-note “lyrics” as normal lines.
- UI: all text in console yellow.
- Output: timings/<slug>.csv with header: line_index,time_secs,text
"""

import sys
import argparse
import curses
import time
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple, Optional

# ─────────────────────────────────────────────
# Bootstrap import path
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
# Arg parsing
# ─────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Curses-based manual lyric timing tool.")
    p.add_argument("--slug", help="Song slug (required for non-interactive use)")
    return p.parse_args()


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def resolve_slug(args: argparse.Namespace) -> str:
    if args.slug:
        slug = args.slug.strip()
        if not slug:
            raise SystemExit("Invalid empty --slug.")
        log("SLUG", f"Using slug '{slug}' (no prompts allowed)", GREEN)
        return slug

    # Legacy fallback if run standalone without 0_master.py
    try:
        slug = input("Enter slug for timing: ").strip()
    except EOFError:
        raise SystemExit("Missing slug (EOF)")
    if not slug:
        raise SystemExit("Slug is required.")
    log("SLUG", f"Using slug '{slug}' (legacy prompt)", YELLOW)
    return slug


def load_lyrics(slug: str) -> List[str]:
    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("TXT", f"Missing lyrics at {txt_path}", RED)
        raise SystemExit(1)

    content = txt_path.read_text(encoding="utf-8").splitlines()
    lines = [ln.rstrip() for ln in content if ln.strip()]
    if not lines:
        log("TXT", "Lyrics file appears empty.", YELLOW)
    return lines


def resolve_audio_path(slug: str) -> Path:
    """
    ALWAYS use MP3 for timing. WAV mixes are never used for timing UI.
    """
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        return mp3
    log("AUDIO", f"MP3 missing for slug '{slug}' at {mp3}", RED)
    raise SystemExit(1)


# ─────────────────────────────────────────────
# Audio transport (ffplay preferred, afplay fallback)
# ─────────────────────────────────────────────
class AudioTransport:
    """
    Transport wrapper with:
      - play from given offset (seek if ffplay available)
      - rewind / fast-forward by N seconds
      - pause / resume
      - current logical song time

    Logical time is maintained independently of the underlying player
    and is always monotonically updated relative to the song.
    """

    def __init__(self, audio_path: Path, rewind_step: float = 5.0) -> None:
        self.audio_path = audio_path
        self.rewind_step = rewind_step

        self._proc: Optional[subprocess.Popen] = None
        self._player_ffplay = shutil.which("ffplay")
        self._player_afplay = shutil.which("afplay")
        self._use_ffplay = self._player_ffplay is not None
        self._supports_seek = self._use_ffplay

        if not self._player_ffplay and not self._player_afplay:
            log("AUDIO", "Neither ffplay nor afplay found in PATH.", RED)
            raise SystemExit(1)

        if self._use_ffplay:
            log("AUDIO", f"Using ffplay for playback (seek-capable): {self._player_ffplay}", CYAN)
        else:
            log("AUDIO", f"Using afplay for playback (no seek; transport limited): {self._player_afplay}", YELLOW)

        # Logical time bookkeeping
        self._logical_pos = 0.0        # seconds into track
        self._state = "stopped"        # "stopped", "playing", "paused"
        self._state_time = 0.0         # monotonic time of last state change

    # ---- internal helpers ----
    def _kill_proc(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    def _update_logical_pos_now(self) -> None:
        """Update logical_pos based on elapsed time while 'playing'."""
        now = time.monotonic()
        if self._state == "playing":
            delta = now - self._state_time
            if delta > 0:
                self._logical_pos += delta
        self._state_time = now

    def _launch_player_at(self, pos: float) -> None:
        """Start underlying player at logical position `pos`."""
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
            # afplay does not support seek; best effort is to restart from 0.
            # Logical time will still jump to requested pos so timestamps remain meaningful.
            if self._logical_pos > 0:
                log("AUDIO", "afplay cannot seek; audio restarts from 0, but timestamps follow logical time.", YELLOW)
            cmd = [self._player_afplay, str(self.audio_path)]

        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as e:
            log("AUDIO", f"Failed to start audio player: {e}", RED)
            raise SystemExit(1)

    # ---- public API ----
    def start(self) -> None:
        """Start playback from 0.0."""
        self._launch_player_at(0.0)

    def current_time(self) -> float:
        """Return current logical song time."""
        self._update_logical_pos_now()
        return max(0.0, self._logical_pos)

    def rewind(self) -> None:
        """Rewind by rewind_step seconds."""
        if not self._supports_seek:
            log("AUDIO", "Rewind requested but seek is not supported (afplay-only). Logical time will move, audio restarts.", YELLOW)
        now = self.current_time()
        new_pos = max(0.0, now - self.rewind_step)
        self._launch_player_at(new_pos)

    def fast_forward(self) -> None:
        """Fast-forward by rewind_step seconds."""
        if not self._supports_seek:
            log("AUDIO", "Fast-forward requested but seek is not supported (afplay-only). Logical time will move, audio restarts.", YELLOW)
        now = self.current_time()
        new_pos = max(0.0, now + self.rewind_step)
        self._launch_player_at(new_pos)

    def adjust_step(self, delta: float) -> None:
        self.rewind_step = max(1.0, min(30.0, self.rewind_step + delta))

    def toggle_pause(self) -> None:
        """Pause or resume playback and logical time."""
        if self._state == "playing":
            # Pause
            self._update_logical_pos_now()
            self._kill_proc()
            self._state = "paused"
            log("AUDIO", "Paused.", YELLOW)
        elif self._state == "paused":
            # Resume
            log("AUDIO", "Resuming.", GREEN)
            self._launch_player_at(self._logical_pos)
        else:
            # If stopped, treat toggle as start from current pos
            log("AUDIO", "Starting from current position.", CYAN)
            self._launch_player_at(self._logical_pos)

    def stop(self) -> None:
        self._kill_proc()
        self._update_logical_pos_now()
        self._state = "stopped"


# ─────────────────────────────────────────────
# Curses UI
# ─────────────────────────────────────────────
NOTE_KEY_MAP = {
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


def curses_main(stdscr, slug: str, lyrics: List[str], audio_path: Path) -> None:
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)   # yellow foreground
    COLOR_Y = curses.color_pair(1)

    # Prepare timing output
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TIMINGS_DIR / f"{slug}.csv"

    # Events: (time_secs, text)
    events: List[Tuple[float, str]] = []

    # Event log (console-style) for bottom of screen
    event_log: List[str] = []

    def add_event(msg: str) -> None:
        event_log.append(msg)
        # keep last 5
        if len(event_log) > 5:
            del event_log[0]

    # Audio transport
    transport = AudioTransport(audio_path, rewind_step=5.0)

    # Initial screen
    stdscr.clear()
    try:
        stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}", COLOR_Y | curses.A_BOLD)
        stdscr.addstr(2, 0, "Controls:", COLOR_Y)
        stdscr.addstr(3, 2, "ENTER : timestamp current lyric", COLOR_Y)
        stdscr.addstr(4, 2, "r     : rewind by N seconds", COLOR_Y)
        stdscr.addstr(5, 2, "+ / - : increase / decrease rewind seconds", COLOR_Y)
        stdscr.addstr(6, 2, "f     : fast-forward by N seconds", COLOR_Y)
        stdscr.addstr(7, 2, "p     : play / pause audio + clock", COLOR_Y)
        stdscr.addstr(8, 2, "1–=   : insert note glyph line at current time", COLOR_Y)
        stdscr.addstr(9, 2, "s     : skip this lyric (no event)", COLOR_Y)
        stdscr.addstr(10, 2, "q     : quit and save CSV", COLOR_Y)
        stdscr.addstr(12, 0, "Press ENTER when ready to start timing + audio playback.", COLOR_Y)
    except curses.error:
        pass
    stdscr.refresh()

    # Wait for user to start
    while True:
        ch = stdscr.getch()
        if ch in (10, 13):  # ENTER
            break

    # Start audio
    transport.start()
    add_event("Timing started; audio playback begun.")

    line_idx = 0
    num_lines = len(lyrics)

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        # Header
        now = transport.current_time()
        try:
            stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}", COLOR_Y | curses.A_BOLD)
            stdscr.addstr(1, 0, f"Time: {now:7.2f}s   Rewind step: {transport.rewind_step:.1f}s", COLOR_Y)
            stdscr.addstr(
                2,
                0,
                "[ENTER] stamp  [r] rewind  [+/-] step  [f] fwd  [p] pause  [1–=] notes  [s] skip  [q] quit",
                COLOR_Y,
            )
        except curses.error:
            pass

        # Lyrics window
        base_row = 4
        log_rows = 5
        window_size = max(3, h - base_row - log_rows - 1)
        offset = max(0, line_idx - window_size // 2)

        for i in range(window_size):
            idx = offset + i
            if idx >= num_lines:
                break
            line = lyrics[idx]
            row = base_row + i
            prefix = f"{idx:3d}: "
            text = (prefix + line)[: max(0, w - 1)]
            try:
                if idx == line_idx:
                    stdscr.addstr(row, 0, text, COLOR_Y | curses.A_REVERSE)
                else:
                    stdscr.addstr(row, 0, text, COLOR_Y)
            except curses.error:
                pass

        # Event log region at bottom
        log_start = base_row + window_size + 1
        if log_start < h:
            try:
                stdscr.addstr(log_start, 0, "-" * max(0, w - 1), COLOR_Y)
            except curses.error:
                pass
            for i, msg in enumerate(event_log[-(h - log_start - 1) :], start=1):
                row = log_start + i
                if row >= h:
                    break
                try:
                    stdscr.addstr(row, 0, msg[: max(0, w - 1)], COLOR_Y)
                except curses.error:
                    pass

        stdscr.refresh()

        ch = stdscr.getch()

        # Quit
        if ch in (ord("q"), ord("Q")):
            add_event("Quit requested; writing CSV and exiting.")
            break

        # Rewind
        if ch in (ord("r"), ord("R")):
            transport.rewind()
            now = transport.current_time()
            add_event(f"Rewind {transport.rewind_step:.1f}s → {now:0.3f}s")
            continue

        # Adjust rewind step
        if ch == ord("+"):
            transport.adjust_step(+1.0)
            add_event(f"Rewind step increased to {transport.rewind_step:.1f}s")
            continue
        if ch == ord("-"):
            transport.adjust_step(-1.0)
            add_event(f"Rewind step decreased to {transport.rewind_step:.1f}s")
            continue

        # Fast-forward
        if ch in (ord("f"), ord("F")):
            transport.fast_forward()
            now = transport.current_time()
            add_event(f"Fast-forward {transport.rewind_step:.1f}s → {now:0.3f}s")
            continue

        # Pause / resume
        if ch in (ord("p"), ord("P")):
            transport.toggle_pause()
            now = transport.current_time()
            add_event(f"Toggle pause/resume at {now:0.3f}s")
            continue

        # Skip lyric (no event recorded)
        if ch in (ord("s"), ord("S")):
            if line_idx < num_lines:
                add_event(f"Skipped line {line_idx}: {lyrics[line_idx][:40]!r}")
                line_idx = min(num_lines, line_idx + 1)
            continue

        # Handle note keys 1–=
        if 0 <= ch <= 255:
            ch_char = chr(ch)
        else:
            ch_char = ""

        if ch_char in NOTE_KEY_MAP:
            note_txt = NOTE_KEY_MAP[ch_char]
            ts = transport.current_time()
            events.append((ts, note_txt))
            add_event(f"[NOTE] {note_txt} inserted at {ts:0.3f}s")
            continue

        # ENTER = stamp current lyric
        if ch in (10, 13):
            if line_idx < num_lines:
                ts = transport.current_time()
                txt = lyrics[line_idx]
                events.append((ts, txt))
                add_event(f"[LINE] {line_idx} stamped at {ts:0.3f}s")
                line_idx += 1
            else:
                add_event("No more lyrics to stamp; ENTER ignored.")
            continue

        # Ignore unknown keys

    # Stop audio
    transport.stop()

    # Build final events sorted by time
    events_sorted = sorted(events, key=lambda x: x[0])

    # Write CSV
    with out_path.open("w", encoding="utf-8") as f:
        f.write("line_index,time_secs,text\n")
        for idx, (ts, text) in enumerate(events_sorted):
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
