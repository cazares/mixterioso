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

BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


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


def load_timings(slug: str) -> list[dict]:
    path = TIMINGS_DIR / f"{slug}.csv"
    if not path.exists():
        raise SystemExit(f"Timings CSV not found: {path}")
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row["line_index"])
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            text = row.get("text", "")
            out.append({"line_index": idx, "time": t, "text": text})
    out.sort(key=lambda r: r["time"])
    return out


class SnippetPlayer:
    def __init__(self, audio_path: Path, start: float, end: float):
        self.audio_path = audio_path
        self.start = max(0.0, start)
        self.end = max(self.start, end)
        self.proc: subprocess.Popen | None = None
        self._t0: float | None = None

    def start_playback(self) -> None:
        self.stop()
        self._t0 = time.monotonic()
        cmd = [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-ss",
            f"{self.start:.3f}",
            "-t",
            f"{self.end - self.start:.3f}",
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
        if self._t0 is None:
            return self.start
        elapsed = time.monotonic() - self._t0
        return self.start + elapsed

    def finished(self) -> bool:
        if self._t0 is None:
            return False
        elapsed = time.monotonic() - self._t0
        return elapsed >= (self.end - self.start) - 0.01


def find_current_event(events: list[dict], pos: float, offset: float) -> int:
    """
    Given absolute audio position 'pos' and global offset,
    return index of current lyric line (or -1 if before first).
    We treat 'pos - offset' as the logical position on the timing axis.
    """
    adjusted = pos - offset
    idx = -1
    for i, e in enumerate(events):
        if e["line_index"] < 0:
            continue
        if e["time"] <= adjusted:
            idx = i
        else:
            break
    return idx


def calibration_ui(
    stdscr,
    slug: str,
    events: list[dict],
    player: SnippetPlayer,
    start_sec: float,
    end_sec: float,
    offset: float,
    offset_path: Path,
):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)

    player.start_playback()
    last_msg = ""
    saved = False

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        pos = player.current_pos()

        stdscr.attron(curses.color_pair(3))
        controls = "[←/→] ±0.05s  [↑/↓] ±0.25s  [SPACE] replay  [s] save  [q] quit"
        stdscr.addstr(0, 0, controls[: w - 1])
        stdscr.attroff(curses.color_pair(3))

        stdscr.attron(curses.color_pair(2))
        header = f"[CAL] slug={slug}  window={fmt_time(start_sec)}–{fmt_time(end_sec)}  offset={offset:+.3f}s"
        stdscr.addstr(1, 0, header[: w - 1])
        stdscr.attroff(curses.color_pair(2))

        idx = find_current_event(events, pos, offset)
        lines = [e for e in events if e["line_index"] >= 0]
        prev_text = ""
        curr_text = "<no line>"
        next_text = ""
        if lines:
            # map idx (over events) to index in lines
            line_pos = -1
            for j, e in enumerate(events):
                if e["line_index"] < 0:
                    continue
                if j == idx:
                    line_pos = len([x for x in lines if x["time"] <= e["time"]]) - 1
                    break
            if line_pos >= 0:
                curr_text = lines[line_pos]["text"]
                if line_pos > 0:
                    prev_text = lines[line_pos - 1]["text"]
                if line_pos + 1 < len(lines):
                    next_text = lines[line_pos + 1]["text"]

        mid = h // 2
        if prev_text and mid - 2 > 1:
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(mid - 2, 0, prev_text[: w - 1])
            stdscr.attroff(curses.color_pair(4))
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(mid, 0, curr_text[: w - 1])
        stdscr.attroff(curses.color_pair(1))
        if next_text and mid + 2 < h - 2:
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(mid + 2, 0, next_text[: w - 1])
            stdscr.attroff(curses.color_pair(5))

        status = f"audio_pos={fmt_time(pos)}"
        stdscr.attron(curses.color_pair(2))
        stdscr.addstr(h - 1, 0, status[: w - 1])
        stdscr.attroff(curses.color_pair(2))

        if last_msg:
            stdscr.attron(curses.color_pair(3))
            stdscr.addstr(h - 2, 0, last_msg[: w - 1])
            stdscr.attroff(curses.color_pair(3))

        stdscr.refresh()

        if player.finished():
            last_msg = "Snippet ended. Press SPACE to replay or adjust offset."
            stdscr.timeout(50)

        stdscr.timeout(100)
        ch = stdscr.getch()
        if ch == -1:
            continue

        if ch in (ord("q"), ord("Q"), 27):
            break

        if ch == ord(" "):
            player.start_playback()
            last_msg = "Replaying snippet"
            continue

        if ch in (curses.KEY_LEFT, ord("h")):
            offset -= 0.05
            last_msg = f"Offset {offset:+.3f}s"
            continue
        if ch in (curses.KEY_RIGHT, ord("l")):
            offset += 0.05
            last_msg = f"Offset {offset:+.3f}s"
            continue
        if ch in (curses.KEY_UP, ord("k")):
            offset += 0.25
            last_msg = f"Offset {offset:+.3f}s"
            continue
        if ch in (curses.KEY_DOWN, ord("j")):
            offset -= 0.25
            last_msg = f"Offset {offset:+.3f}s"
            continue

        if ch in (ord("s"), ord("S")):
            OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
            data = {"offset": offset}
            offset_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            last_msg = f"Saved offset {offset:+.3f}s to {offset_path}"
            saved = True
            stdscr.refresh()
            time.sleep(0.8)
            break

    player.stop()
    return saved, offset


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Audio/video offset calibration.")
    p.add_argument("slug", help="Song slug (e.g. 'californication')")
    p.add_argument(
        "--start",
        type=str,
        default="00:30",
        help="Snippet start time (mm:ss or seconds, default 00:30)",
    )
    p.add_argument(
        "--end",
        type=str,
        default="01:00",
        help="Snippet end time (mm:ss or seconds, default 01:00)",
    )
    return p.parse_args(argv or sys.argv[1:])


def main(argv=None):
    args = parse_args(argv)
    slug = slugify(args.slug)

    try:
        start_sec = parse_time_string(args.start)
        end_sec = parse_time_string(args.end)
    except Exception as e:
        raise SystemExit(f"Invalid start/end time: {e}")
    if end_sec <= start_sec:
        raise SystemExit("End time must be greater than start time.")

    audio_path = MP3_DIR / f"{slug}.mp3"
    if not audio_path.exists():
        raise SystemExit(f"Audio not found: {audio_path}")

    events = load_timings(slug)
    if not events:
        raise SystemExit("No timing events found; run timing step first.")

    OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
    offset_path = OFFSETS_DIR / f"{slug}.json"

    offset = 0.0
    if offset_path.exists():
        try:
            data = json.loads(offset_path.read_text(encoding="utf-8"))
            offset = float(data.get("offset", 0.0))
            log("CAL", f"Existing offset loaded: {offset:+.3f}s", GREEN)
        except Exception:
            log("CAL", f"Failed to parse existing offset file: {offset_path}", YELLOW)

    player = SnippetPlayer(audio_path, start_sec, end_sec)
    log("CAL", f"Audio={audio_path}, slug={slug}, window={fmt_time(start_sec)}–{fmt_time(end_sec)}", GREEN)

    saved, final_offset = curses.wrapper(
        calibration_ui,
        slug,
        events,
        player,
        start_sec,
        end_sec,
        offset,
        offset_path,
    )

    if saved:
        log("CAL", f"Final offset saved for {slug}: {final_offset:+.3f}s", GREEN)
    else:
        log("CAL", "Exited without saving new offset.", YELLOW)


if __name__ == "__main__":
    main()

# end of 4_calibrate.py
