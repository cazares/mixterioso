#!/usr/bin/env python3
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import curses

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
MP3_DIR = PROJECT_ROOT / "mp3s"
TIMING_DIR = PROJECT_ROOT / "timings"
OFFSET_DIR = PROJECT_ROOT / "offsets"

DEFAULT_WINDOW_START = 30.0
DEFAULT_WINDOW_LEN = 30.0

# Offset step sizes (seconds)
STEP_FINE = 0.125          # < / >
STEP_MED = 0.25            # [ / ]
STEP_EXTRA_FINE = STEP_FINE / 2.0  # , / .  => 0.0625


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    rem = sec - m * 60
    s = int(rem)
    ms = int(round((rem - s) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    if s == 60:
        m += 1
        s = 0
    return f"{m:02d}:{s:02d}.{ms:03d}"


def parse_time_string(s: str) -> float:
    s = s.strip()
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    if len(parts) == 2:
        m, sec = parts
        return int(m) * 60 + float(sec)
    if len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)
    raise ValueError(f"Cannot parse time string: {s}")


def ffprobe_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    out = subprocess.check_output(cmd, text=True).strip()
    try:
        return float(out)
    except ValueError:
        raise SystemExit(f"Could not parse duration from ffprobe output: {out!r}")


def load_timings(slug: str) -> tuple[list[dict], Path]:
    timing_path = TIMING_DIR / f"{slug}.csv"
    if not timing_path.exists():
        return [], timing_path

    events: list[dict] = []
    with timing_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "")
            idx = int(row.get("line_index", -1) or -1)
            events.append({"time": t, "text": text, "line_index": idx})
    events.sort(key=lambda e: e["time"])
    return events, timing_path


def load_offset(slug: str) -> tuple[float, Path]:
    OFFSET_DIR.mkdir(parents=True, exist_ok=True)
    offset_path = OFFSET_DIR / f"{slug}.json"
    if not offset_path.exists():
        return 0.0, offset_path
    try:
        data = json.loads(offset_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            if "offset_secs" in data:
                return float(data["offset_secs"]), offset_path
            if "offset" in data:
                return float(data["offset"]), offset_path
        if isinstance(data, (int, float)):
            return float(data), offset_path
    except Exception:
        pass
    return 0.0, offset_path


class SnippetPlayer:
    def __init__(self, audio_path: Path, win_start: float, win_end: float):
        self.audio_path = audio_path
        self.win_start = win_start
        self.win_end = win_end
        self.length = max(0.0, win_end - win_start)
        self.proc: subprocess.Popen | None = None
        self.start_wall: float | None = None
        self.paused = False
        self._paused_pos: float | None = None

    def start(self, at_local: float = 0.0) -> None:
        self.stop()
        at_local = max(0.0, min(self.length, at_local))
        start = self.win_start + at_local
        self.paused = False
        self._paused_pos = None
        self.start_wall = time.monotonic()
        cmd = [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{self.length:.3f}",
            "-i",
            str(self.audio_path),
            "-loglevel",
            "quiet",
        ]
        self.proc = subprocess.Popen(cmd)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass
        self.proc = None

    def current_pos(self) -> float:
        if self.paused:
            return self._paused_pos or 0.0
        if self.start_wall is None:
            return 0.0
        if self.proc is not None and self.proc.poll() is None:
            return min(self.length, time.monotonic() - self.start_wall)
        return min(self.length, time.monotonic() - self.start_wall)

    def finished(self) -> bool:
        if self.paused:
            return False
        return self.current_pos() >= self.length - 0.01

    def pause_toggle(self) -> None:
        if not self.paused:
            self._paused_pos = self.current_pos()
            self.stop()
            self.paused = True
        else:
            pos = self._paused_pos or 0.0
            self.start(pos)
            self.paused = False

    def restart(self) -> None:
        self.start(0.0)


def find_current_event(events: list[dict], t_effective: float) -> int | None:
    idx = None
    for i, ev in enumerate(events):
        if ev["time"] <= t_effective:
            idx = i
        else:
            break
    return idx


def calibrate_ui(
    stdscr,
    events,
    player: SnippetPlayer,
    slug: str,
    offset: float,
    win_start: float,
    win_end: float,
):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)
    curses.init_pair(6, curses.COLOR_YELLOW, -1)

    player.restart()
    last_msg = ""
    cur_offset = offset

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        controls1 = (
            "[<]/[>] ±0.125s  [[/]] ±0.25s  [,/ .] ±0.0625s  "
            "[0] reset  [r] restart  [p] pause  [s] save  [q]/ESC quit"
        )
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(0, 0, controls1[: w - 1])
        stdscr.attroff(curses.color_pair(3))

        pos_local = player.current_pos()
        t_global = win_start + pos_local
        t_effective = t_global + cur_offset

        idx = find_current_event(events, t_effective)
        prev_ev = events[idx - 1] if (idx is not None and idx > 0) else None
        cur_ev = events[idx] if idx is not None else None
        next_ev = events[idx + 1] if (idx is not None and idx + 1 < len(events)) else None

        mid = h // 2

        def draw_line(row, text, color_pair):
            if 0 <= row < h:
                stdscr.attron(curses.color_pair(color_pair))
                stdscr.addstr(row, 0, (text or "")[: w - 1])
                stdscr.attroff(curses.color_pair(color_pair))

        if prev_ev:
            draw_line(mid - 2, prev_ev["text"], 4)
        draw_line(mid, cur_ev["text"] if cur_ev else "<no event>", 1)
        if next_ev:
            draw_line(mid + 2, next_ev["text"], 5)

        if last_msg and h >= 3:
            stdscr.attron(curses.color_pair(3))
            stdscr.addstr(h - 2, 0, last_msg[: w - 1])
            stdscr.attroff(curses.color_pair(3))

        status = (
            f"[CAL] slug={slug}  win={fmt_time(win_start)}–{fmt_time(win_end)}  "
            f"t_win={fmt_time(pos_local)}  t_glob={fmt_time(t_global)}  "
            f"offset={cur_offset:+.3f}s"
        )
        stdscr.attron(curses.color_pair(2))
        stdscr.addstr(h - 1, 0, status[: w - 1])
        stdscr.attroff(curses.color_pair(2))

        stdscr.refresh()

        if player.finished():
            player.restart()

        stdscr.timeout(50)
        ch = stdscr.getch()
        if ch == -1:
            continue

        if ch in (ord("q"), ord("Q"), 27):
            return None  # no change

        if ch in (ord("s"), ord("S")):
            return cur_offset

        if ch in (ord("p"), ord("P")):
            player.pause_toggle()
            last_msg = "Paused" if player.paused else "Resumed"
            continue

        if ch in (ord("r"), ord("R")):
            player.restart()
            last_msg = "Restarted snippet"
            continue

        if ch == ord("0"):
            cur_offset = 0.0
            last_msg = "Offset reset to 0.000s"
            continue

        # fine: 0.125s
        if ch in (ord("<"), curses.KEY_LEFT):
            cur_offset -= STEP_FINE
            last_msg = f"Offset {cur_offset:+.3f}s (fine -)"
            continue

        if ch in (ord(">"), curses.KEY_RIGHT):
            cur_offset += STEP_FINE
            last_msg = f"Offset {cur_offset:+.3f}s (fine +)"
            continue

        # medium: 0.25s
        if ch == ord("["):
            cur_offset -= STEP_MED
            last_msg = f"Offset {cur_offset:+.3f}s (med -)"
            continue

        if ch == ord("]"):
            cur_offset += STEP_MED
            last_msg = f"Offset {cur_offset:+.3f}s (med +)"
            continue

        # extra fine: 0.0625s
        if ch == ord(","):
            cur_offset -= STEP_EXTRA_FINE
            last_msg = f"Offset {cur_offset:+.4f}s (x-fine -)"
            continue

        if ch == ord("."):
            cur_offset += STEP_EXTRA_FINE
            last_msg = f"Offset {cur_offset:+.4f}s (x-fine +)"
            continue


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Offset calibration (step 4).")
    p.add_argument("slug", help="Song slug, e.g. under_the_bridge")
    p.add_argument("--start", help="Window start (mm:ss or seconds)", default=None)
    p.add_argument("--end", help="Window end (mm:ss or seconds)", default=None)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    slug = args.slug.strip()
    if not slug:
        raise SystemExit("Slug is required.")

    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log("CAL", f"Audio not found: {mp3_path}", RED)
        sys.exit(1)

    events, timing_path = load_timings(slug)
    if not timing_path.exists():
        log("CAL", f"Timings CSV not found: {timing_path}", YELLOW)
        return

    if not events:
        log("CAL", f"No timing rows in {timing_path}, skipping calibration.", YELLOW)
        return

    duration = ffprobe_duration(mp3_path)

    if args.start:
        try:
            win_start = parse_time_string(args.start)
        except Exception:
            win_start = DEFAULT_WINDOW_START
    else:
        win_start = DEFAULT_WINDOW_START

    if args.end:
        try:
            win_end = parse_time_string(args.end)
        except Exception:
            win_end = win_start + DEFAULT_WINDOW_LEN
    else:
        win_end = win_start + DEFAULT_WINDOW_LEN

    win_start = max(0.0, min(win_start, max(0.0, duration - 0.1)))
    win_end = max(win_start + 0.1, min(win_end, duration))

    offset, offset_path = load_offset(slug)
    if offset_path.exists():
        log("CAL", f"Existing offset loaded: {offset:+.3f}s", GREEN)
    else:
        log("CAL", "No existing offset JSON, starting from 0.000s", YELLOW)

    log(
        "CAL",
        f"Audio={mp3_path}, slug={slug}, window={fmt_time(win_start)}–{fmt_time(win_end)}",
        GREEN,
    )

    player = SnippetPlayer(mp3_path, win_start, win_end)

    try:
        new_offset = curses.wrapper(
            calibrate_ui, events, player, slug, offset, win_start, win_end
        )
    finally:
        player.stop()

    if new_offset is None:
        log("CAL", "Exited without saving new offset.", YELLOW)
        return

    data = {
        "slug": slug,
        "offset_secs": float(new_offset),
        "window_start_secs": float(win_start),
        "window_end_secs": float(win_end),
    }
    OFFSET_DIR.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("CAL", f"Final offset saved for {slug}: {new_offset:+.3f}s", GREEN)


if __name__ == "__main__":
    main()

# end of 4calibrate.py
