#!/usr/bin/env python3
import argparse
import csv
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import curses

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def infer_slug(txt_path: Path | None, audio_path: Path) -> str:
    if txt_path is not None:
        return slugify(txt_path.stem)
    return slugify(audio_path.stem)


def load_lyrics(txt_path: Path) -> list[str]:
    text = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines()]
    return [ln for ln in lines if ln]


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


def load_timings(timing_path: Path, num_lines: int) -> list[dict]:
    if not timing_path.exists():
        return []
    out = []
    with timing_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                idx = int(row["line_index"])
                t = float(row["time_secs"])
            except (KeyError, ValueError):
                continue
            if 0 <= idx < num_lines:
                out.append({"line_index": idx, "time": t})
    out.sort(key=lambda r: r["time"])
    return out


class AudioPlayer:
    def __init__(self, audio_path: Path):
        self.audio_path = audio_path
        self.proc = None
        self.offset = 0.0
        self.start_wall = None
        self.duration = ffprobe_duration(audio_path)

    def start(self, offset: float) -> None:
        self.stop()
        self.offset = max(0.0, min(self.duration, offset))
        self.start_wall = time.monotonic()
        cmd = [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-ss",
            f"{self.offset:.3f}",
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
        if self.start_wall is None:
            return self.offset
        if self.proc is not None and self.proc.poll() is None:
            return min(self.duration, self.offset + (time.monotonic() - self.start_wall))
        return min(self.duration, self.offset + (time.monotonic() - self.start_wall))

    def finished(self) -> bool:
        return self.current_pos() >= self.duration - 0.01


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m:02d}:{s:05.2f}"


def timing_ui(stdscr, lyrics, timings, history, player: AudioPlayer, timing_path: Path, rewind_step: float, start_pos: float):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLUE)   # current line
    curses.init_pair(2, curses.COLOR_CYAN, -1)                   # header
    curses.init_pair(3, curses.COLOR_YELLOW, -1)                 # controls
    curses.init_pair(4, curses.COLOR_GREEN, -1)                  # previous
    curses.init_pair(5, curses.COLOR_MAGENTA, -1)                # next

    num_lines = len(lyrics)

    def recompute_current_index(pos: float) -> int:
        if not timings:
            return 0
        past = [t for t in timings if t["time"] <= pos]
        if not past:
            return 0
        return min(max(t["line_index"] for t in past) + 1, num_lines)

    current_index = recompute_current_index(start_pos)
    player.start(start_pos)

    aborted = False
    saving = False

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        pos = player.current_pos()
        top = f"[TIMING] {timing_path.name}  pos={fmt_time(pos)}/{fmt_time(player.duration)}  line={current_index+1}/{num_lines}"
        stdscr.attron(curses.color_pair(2))
        stdscr.addstr(0, 0, top[: w - 1])
        stdscr.attroff(curses.color_pair(2))

        prev_line = lyrics[current_index - 1] if current_index > 0 else ""
        curr_line = lyrics[current_index] if current_index < num_lines else "<done>"
        next_line = lyrics[current_index + 1] if current_index + 1 < num_lines else ""

        mid = h // 2
        if prev_line:
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(mid - 2, 0, prev_line[: w - 1])
            stdscr.attroff(curses.color_pair(4))
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(mid, 0, curr_line[: w - 1])
        stdscr.attroff(curses.color_pair(1))
        if next_line:
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(mid + 2, 0, next_line[: w - 1])
            stdscr.attroff(curses.color_pair(5))

        controls = "[SPACE/ENTER] tag  [1] rewind  [0] undo-rewind  [g] goto time  [q] save+quit  [ESC] abort"
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(h - 1, 0, controls[: w - 1])
        stdscr.attroff(curses.color_pair(3))

        stdscr.refresh()

        if player.finished():
            break

        stdscr.timeout(50)
        ch = stdscr.getch()
        if ch == -1:
            continue

        if ch == 27:  # ESC
            aborted = True
            break
        if ch in (ord("q"), ord("Q")):
            saving = True
            break
        if ch in (ord(" "), 10, 13):
            if current_index < num_lines:
                timings.append({"line_index": current_index, "time": player.current_pos()})
                current_index = min(current_index + 1, num_lines)
            continue
        if ch == ord("1"):
            pos = player.current_pos()
            if pos <= 0.1:
                continue
            snapshot = (pos, deepcopy(timings), current_index)
            history.append(snapshot)
            new_pos = max(0.0, pos - rewind_step)
            timings[:] = [t for t in timings if t["time"] <= new_pos]
            current_index = recompute_current_index(new_pos)
            player.start(new_pos)
            continue
        if ch == ord("0"):
            if not history:
                continue
            prev_pos, prev_timings, prev_idx = history.pop()
            timings[:] = prev_timings
            current_index = prev_idx
            player.start(prev_pos)
            continue
        if ch in (ord("g"), ord("G")):
            curses.echo()
            stdscr.addstr(h - 2, 0, "Go to time (mm:ss or seconds): ")
            stdscr.clrtoeol()
            stdscr.refresh()
            s = stdscr.getstr(h - 2, 32).decode("utf-8")
            curses.noecho()
            try:
                tgt = parse_time_string(s)
            except Exception:
                continue
            tgt = max(0.0, min(player.duration, tgt))
            current_index = recompute_current_index(tgt)
            player.start(tgt)
            continue

    return aborted, saving


def write_timings(timing_path: Path, lyrics, timings) -> None:
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    timings_sorted = sorted(timings, key=lambda t: t["line_index"])
    with timing_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "time_secs", "text"])
        for t in timings_sorted:
            idx = t["line_index"]
            sec = t["time"]
            text = lyrics[idx] if 0 <= idx < len(lyrics) else ""
            writer.writerow([idx, f"{sec:.3f}", text])


def parse_args(argv):
    p = argparse.ArgumentParser(description="Interactive timing editor.")
    p.add_argument("--txt", type=str, required=True, help="Lyrics txt path")
    p.add_argument("--audio", type=str, required=True, help="Audio file to play")
    p.add_argument("--timings", type=str, help="Timings CSV path")
    p.add_argument("--rewind-step", type=float, default=5.0, help="Rewind seconds for key '1'")
    p.add_argument("--start", type=str, help="Start time (mm:ss or seconds)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    txt_path = Path(args.txt).resolve()
    audio_path = Path(args.audio).resolve()
    if not txt_path.exists():
        raise SystemExit(f"Lyrics not found: {txt_path}")
    if not audio_path.exists():
        raise SystemExit(f"Audio not found: {audio_path}")

    slug = infer_slug(txt_path, audio_path)
    if args.timings:
        timing_path = Path(args.timings).resolve()
    else:
        timing_path = Path("timings") / f"{slug}.csv"

    lyrics = load_lyrics(txt_path)
    if not lyrics:
        raise SystemExit("No lyrics lines found.")

    timings = load_timings(timing_path, len(lyrics))
    history = []

    player = AudioPlayer(audio_path)
    if args.start:
        try:
            start_pos = parse_time_string(args.start)
        except Exception:
            start_pos = 0.0
    else:
        start_pos = 0.0

    log("TIMING", f"Lyrics={txt_path}, audio={audio_path}, timings={timing_path}", GREEN)
    log("TIMING", f"Lines={len(lyrics)}, existing timings={len(timings)}", GREEN)

    aborted, saving = curses.wrapper(
        timing_ui,
        lyrics,
        timings,
        history,
        player,
        timing_path,
        args.rewind_step,
        start_pos,
    )

    player.stop()

    if aborted:
        log("TIMING", "Aborted without saving.", YELLOW)
        return
    if saving or timings:
        write_timings(timing_path, lyrics, timings)
        log("TIMING", f"Saved {len(timings)} timings to {timing_path}", GREEN)
    else:
        log("TIMING", "No timings to save.", YELLOW)


if __name__ == "__main__":
    main()
