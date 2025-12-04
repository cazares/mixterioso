#!/usr/bin/env python3
"""
3_timing.py — Curses-based manual lyric timing tool (NO AI)

Features:
- STRICT slug mode when --slug provided.
- Auto audio playback (MP3 only).
- Global clock with seek-aware logic.
- Rewind (r), Fast-forward (f), Pause/Resume (p).
- Adjustable rewind step (+/-).
- Insert musical notes (1–=) as lyric events.
- Scrolling event log.
- Entire UI in console yellow.
"""

import sys
import argparse
import curses
import time
import subprocess
import shutil
from pathlib import Path
from typing import List, Tuple

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
# Arg parsing
# ─────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Curses timing tool")
    p.add_argument("--slug")
    return p.parse_args()

# ─────────────────────────────────────────────
# Resolve slug
# ─────────────────────────────────────────────
def resolve_slug(args) -> str:
    if args.slug:
        slug = args.slug.strip()
        if not slug:
            raise SystemExit("Invalid empty slug.")
        log("SLUG", f"Using slug '{slug}' (strict mode)", GREEN)
        return slug

    # Legacy fallback if launched independently
    try:
        slug = input("Enter slug for timing: ").strip()
    except EOFError:
        raise SystemExit("Missing slug.")
    if not slug:
        raise SystemExit("Slug required.")
    log("SLUG", f"Using slug '{slug}' (legacy)", YELLOW)
    return slug

# ─────────────────────────────────────────────
# Load lyrics
# ─────────────────────────────────────────────
def load_lyrics(slug: str) -> List[str]:
    p = TXT_DIR / f"{slug}.txt"
    if not p.exists():
        log("TXT", f"Missing {p}", RED)
        raise SystemExit(1)
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(ln)
    return out

# ─────────────────────────────────────────────
# Ensure MP3 path
# ─────────────────────────────────────────────
def resolve_audio_path(slug: str) -> Path:
    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        log("AUDIO", f"Missing MP3 at {mp3}", RED)
        raise SystemExit(1)
    return mp3

# ─────────────────────────────────────────────
# Audio Transport
# ─────────────────────────────────────────────
class AudioTransport:
    def __init__(self, audio_path: Path, rewind_step=5.0):
        self.audio_path = audio_path
        self.rewind_step = rewind_step

        self._proc: subprocess.Popen | None = None
        self._player_ffplay = shutil.which("ffplay")
        self._player_afplay = shutil.which("afplay")
        self._use_ffplay = self._player_ffplay is not None
        self._supports_seek = self._use_ffplay

        if not self._player_ffplay and not self._player_afplay:
            log("AUDIO", "Neither ffplay nor afplay found.", RED)
            raise SystemExit(1)

        if self._use_ffplay:
            log("AUDIO", "Using ffplay (seek-capable)", CYAN)
        else:
            log("AUDIO", "Using afplay (no seek)", YELLOW)

        # logical playback state
        self._logical_pos = 0.0
        self._state = "stopped"    # playing / paused / stopped
        self._state_time = 0.0     # last monotonic update

    def _kill_proc(self):
        if self._proc:
            try: self._proc.terminate()
            except: pass
            self._proc = None

    def _update_logical(self):
        now = time.monotonic()
        if self._state == "playing":
            self._logical_pos += (now - self._state_time)
        self._state_time = now

    def _launch(self, pos: float):
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
            if self._logical_pos > 0:
                log("AUDIO", "afplay cannot seek; audio restarts but logical time preserved.", YELLOW)
            cmd = [self._player_afplay, str(self.audio_path)]

        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as e:
            log("AUDIO", f"Failed to play: {e}", RED)
            raise SystemExit(1)

    # Public API
    def start(self):
        self._launch(0.0)

    def current_time(self) -> float:
        self._update_logical()
        return max(0.0, self._logical_pos)

    def rewind(self):
        now = self.current_time()
        new = max(0.0, now - self.rewind_step)
        self._launch(new)

    def fast_forward(self):
        now = self.current_time()
        new = now + self.rewind_step
        self._launch(new)

    def adjust_step(self, delta):
        self.rewind_step = max(1, min(30, self.rewind_step + delta))

    def toggle_pause(self):
        if self._state == "playing":
            self._update_logical()
            self._kill_proc()
            self._state = "paused"
        elif self._state == "paused":
            self._launch(self._logical_pos)
        else:
            self._launch(self._logical_pos)

    def stop(self):
        self._kill_proc()
        self._update_logical()
        self._state = "stopped"

# ─────────────────────────────────────────────
# Note glyphs
# ─────────────────────────────────────────────
NOTE_KEY_MAP = {
    "1": "♪","2": "♫","3": "♬","4": "♩","5": "♪♫","6": "♫♬",
    "7": "♬♩","8": "♪♩","9": "♪♬","0": "♫♪","-": "♩♬♪","=": "♫♪♬♩"
}

# ─────────────────────────────────────────────
# Curses UI
# ─────────────────────────────────────────────
def curses_main(stdscr, slug, lyrics, audio_path: Path):

    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_YELLOW, -1)
    COLOR_Y = curses.color_pair(1)

    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TIMINGS_DIR / f"{slug}.csv"

    events: List[tuple[float,str]] = []
    event_log: List[str] = []

    def log_event(msg: str):
        event_log.append(msg)
        if len(event_log) > 6:
            del event_log[0]

    transport = AudioTransport(audio_path, rewind_step=5.0)

    # pre-start screen
    stdscr.clear()
    try:
        stdscr.addstr(0, 0, f"Mixterioso Timing – {slug}", COLOR_Y | curses.A_BOLD)
        stdscr.addstr(2, 0, "Controls:", COLOR_Y)
        stdscr.addstr(3, 2, "ENTER = stamp lyric", COLOR_Y)
        stdscr.addstr(4, 2, "r = rewind", COLOR_Y)
        stdscr.addstr(5, 2, "+/- = adjust rewind seconds", COLOR_Y)
        stdscr.addstr(6, 2, "f = fast-forward", COLOR_Y)
        stdscr.addstr(7, 2, "p = pause/resume", COLOR_Y)
        stdscr.addstr(8, 2, "1–= = insert note event", COLOR_Y)
        stdscr.addstr(9, 2, "s = skip lyric", COLOR_Y)
        stdscr.addstr(10,2, "q = quit/save", COLOR_Y)
        stdscr.addstr(12,0, "Press ENTER to start playback + timing.", COLOR_Y)
    except: pass
    stdscr.refresh()

    # Wait for start
    while True:
        ch = stdscr.getch()
        if ch in (10, 13):
            break

    transport.start()
    log_event("Timing started; audio playing")

    idx = 0
    n = len(lyrics)

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        tnow = transport.current_time()

        # header
        try:
            stdscr.addstr(0,0,f"Mixterioso Timing – {slug}", COLOR_Y | curses.A_BOLD)
            stdscr.addstr(1,0,f"Time: {tnow:7.2f}s   Step: {transport.rewind_step:.1f}s", COLOR_Y)
            stdscr.addstr(
                2,0,
                "[ENTER]stamp [r]rew [+/−]step [f]fwd [p]pause [1–=]notes [s]skip [q]quit",
                COLOR_Y
            )
        except: pass

        # lyric window
        base = 4
        log_rows = 6
        win = max(3, h - base - log_rows - 1)
        off = max(0, idx - win//2)

        for i in range(win):
            li = off + i
            if li >= n: break
            line = f"{li:3d}: {lyrics[li]}"
            row = base + i
            if li == idx:
                mode = COLOR_Y | curses.A_REVERSE
            else:
                mode = COLOR_Y
            try:
                stdscr.addstr(row, 0, line[:w-1], mode)
            except: pass

        # event log
        start = base + win + 1
        if start < h:
            try:
                stdscr.addstr(start,0,"-"*(w-1),COLOR_Y)
            except: pass
            logs = event_log[-(h-start-1):]
            for i,msg in enumerate(logs,1):
                row = start + i
                if row >= h: break
                try:
                    stdscr.addstr(row,0,msg[:w-1],COLOR_Y)
                except: pass

        stdscr.refresh()

        ch = stdscr.getch()

        if ch in (ord("q"), ord("Q")):
            log_event("Quitting – saving CSV")
            break

        # Rewind
        if ch in (ord("r"), ord("R")):
            transport.rewind()
            log_event(f"Rewind → {transport.current_time():.3f}s")
            continue

        # Step adjust
        if ch == ord("+"):
            transport.adjust_step(+1)
            log_event(f"Step = {transport.rewind_step:.1f}s")
            continue
        if ch == ord("-"):
            transport.adjust_step(-1)
            log_event(f"Step = {transport.rewind_step:.1f}s")
            continue

        # Fast-forward
        if ch in (ord("f"), ord("F")):
            transport.fast_forward()
            log_event(f"Fwd → {transport.current_time():.3f}s")
            continue

        # Pause / resume
        if ch in (ord("p"), ord("P")):
            transport.toggle_pause()
            log_event(f"Pause toggle @ {transport.current_time():.3f}s")
            continue

        # Skip lyric
        if ch in (ord("s"), ord("S")):
            if idx < n:
                log_event(f"Skip line {idx}")
                idx += 1
            continue

        # Notes
        if 0 <= ch <= 255:
            cchar = chr(ch)
        else:
            cchar = ""

        if cchar in NOTE_KEY_MAP:
            txt = NOTE_KEY_MAP[cchar]
            ts = transport.current_time()
            events.append((ts, txt))
            log_event(f"[NOTE] {txt} @ {ts:0.3f}s")
            continue

        # ENTER = stamp lyric
        if ch in (10, 13):
            if idx < n:
                ts = transport.current_time()
                events.append((ts, lyrics[idx]))
                log_event(f"[LINE] {idx} @ {ts:0.3f}s")
                idx += 1
            else:
                log_event("No more lyrics")
            continue

        # ignore everything else

    transport.stop()

    # write CSV
    events_sorted = sorted(events, key=lambda x: x[0])
    with out_path.open("w", encoding="utf-8") as f:
        f.write("line_index,time_secs,text\n")
        for i,(ts,txt) in enumerate(events_sorted):
            f.write(f"{i},{ts:.6f},{txt}\n")

    log("CSV", f"Wrote {out_path}", GREEN)

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    log("MODE","Curses Timing Tool",CYAN)
    args = parse_args()
    slug = resolve_slug(args)
    lyrics = load_lyrics(slug)
    audio = resolve_audio_path(slug)

    try:
        curses.wrapper(curses_main, slug, lyrics, audio)
    except KeyboardInterrupt:
        log("ABORT","Ctrl+C",YELLOW)

if __name__ == "__main__":
    main()

# end of 3_timing.py
