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
    out: list[dict] = []
    with timing_path.open("r", encoding="utf-8") as f:
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


class AudioPlayer:
    def __init__(self, audio_path: Path):
        self.audio_path = audio_path
        self.proc: subprocess.Popen | None = None
        self.offset = 0.0
        self.start_wall: float | None = None
        self.duration = ffprobe_duration(audio_path)
        self.paused = False
        self._paused_pos: float | None = None

    def start(self, offset: float) -> None:
        self.stop()
        self.paused = False
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
        if self.paused:
            if self._paused_pos is not None:
                return self._paused_pos
            return self.offset
        if self.start_wall is None:
            return self.offset
        if self.proc is not None and self.proc.poll() is None:
            return min(self.duration, self.offset + (time.monotonic() - self.start_wall))
        return min(self.duration, self.offset + (time.monotonic() - self.start_wall))

    def finished(self) -> bool:
        if self.paused:
            return False
        return self.current_pos() >= self.duration - 0.01

    def pause_toggle(self) -> None:
        if not self.paused:
            self._paused_pos = self.current_pos()
            self.stop()
            self.paused = True
        else:
            pos = self._paused_pos if self._paused_pos is not None else self.current_pos()
            self.start(pos)
            self.paused = False

    def restart(self) -> None:
        self._paused_pos = 0.0
        self.start(0.0)
        self.paused = False


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


NOTE_KEYS = {
    ord("1"): "♫",
    ord("2"): "♪",
    ord("3"): "♬",
    ord("4"): "♩",
    ord("5"): "♫♪♬♩",
    ord("6"): "♫♪♫♪",
    ord("7"): "♬♩♬♩",
    ord("8"): "♫♫♫♫",
    ord("9"): "♪♪♪♪",
    ord("0"): "𝄞 ♪ 𝄢 ♫",
    ord("-"): "𝄞 𝄢 🤍 𝄢 𝄞",
    ord("="): "𝄞 ♡ 𝄢",
}


def prompt_yes_no(stdscr, question: str) -> bool:
    h, w = stdscr.getmaxyx()
    stdscr.timeout(-1)
    stdscr.move(h - 2, 0)
    stdscr.clrtoeol()
    stdscr.attron(curses.color_pair(3))
    stdscr.addstr(h - 2, 0, question[: w - 1])
    stdscr.attroff(curses.color_pair(3))
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("y"), ord("Y")):
            stdscr.timeout(50)
            return True
        if ch in (ord("n"), ord("N"), 10, 13, 27):
            stdscr.timeout(50)
            return False


def prompt_clear_mode(stdscr, pos_str: str) -> int:
    h, w = stdscr.getmaxyx()
    stdscr.timeout(-1)
    msg = f"Clear timings? [1] all  [2] 0–{pos_str}  [3/N] keep: "
    stdscr.move(h - 2, 0)
    stdscr.clrtoeol()
    stdscr.attron(curses.color_pair(3))
    stdscr.addstr(h - 2, 0, msg[: w - 1])
    stdscr.attroff(curses.color_pair(3))
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch == ord("1"):
            stdscr.timeout(50)
            return 1
        if ch == ord("2"):
            stdscr.timeout(50)
            return 2
        if ch in (ord("3"), ord("n"), ord("N"), 10, 13, 27):
            stdscr.timeout(50)
            return 3


def timing_ui(
    stdscr,
    lyrics,
    timings,
    history,
    player: AudioPlayer,
    timing_path: Path,
    rewind_step: float,
    start_pos: float,
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

    num_lines = len(lyrics)

    def recompute_current_index(pos: float) -> int:
        if not timings:
            return 0
        past = [t for t in timings if t["time"] <= pos and t["line_index"] >= 0]
        if not past:
            return 0
        return min(max(t["line_index"] for t in past) + 1, num_lines)

    current_index = recompute_current_index(start_pos)
    player.start(start_pos)

    aborted = False
    saving = False
    last_msg = ""

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        pos = player.current_pos()

        controls1 = (
            "[SPACE/ENTER] tag  "
            "[<] rewind  [u] undo  [>] ff  [r] restart  [g] goto  "
            "[q] save+quit  [ESC] abort"
        )
        controls2 = (
            "[1]♫ [2]♪ [3]♬ [4]♩ [5]♫♪♬♩ [6]♫♪♫♪ [7]♬♩♬♩ "
            "[8]♫♫♫♫ [9]♪♪♪♪ [0]𝄞♪𝄢♫ [-]𝄞𝄢🤍𝄢𝄞 [=]𝄞♡𝄢"
        )
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(0, 0, controls1[: w - 1])
        if h > 2:
            stdscr.addstr(1, 0, controls2[: w - 1])
        stdscr.attroff(curses.color_pair(3))

        mid = h // 2
        prev_line = lyrics[current_index - 1] if current_index > 0 else ""
        curr_line = lyrics[current_index] if current_index < num_lines else "<done>"
        next_line = lyrics[current_index + 1] if current_index + 1 < num_lines else ""

        if prev_line and mid - 2 > 1:
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(mid - 2, 0, prev_line[: w - 1])
            stdscr.attroff(curses.color_pair(4))
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(mid, 0, curr_line[: w - 1])
        stdscr.attroff(curses.color_pair(1))
        if next_line and mid + 2 < h - 2:
            stdscr.attron(curses.color_pair(5))
            stdscr.addstr(mid + 2, 0, next_line[: w - 1])
            stdscr.attroff(curses.color_pair(5))

        if last_msg and h >= 3:
            stdscr.attron(curses.color_pair(3))
            stdscr.addstr(h - 2, 0, last_msg[: w - 1])
            stdscr.attroff(curses.color_pair(3))

        status_left = f"[TIMING] {timing_path.name}  "
        pos_str = f"pos={fmt_time(pos)}/{fmt_time(player.duration)}"
        status_right = f"  line={current_index+1}/{num_lines}"
        if player.paused:
            status_right += "  [PAUSED]"

        row = h - 1
        col = 0
        stdscr.attron(curses.color_pair(2))
        if col < w:
            s = status_left[: w - col - 1]
            stdscr.addstr(row, col, s)
            col += len(s)
        stdscr.attroff(curses.color_pair(2))

        if col < w:
            stdscr.attron(curses.color_pair(6) | curses.A_BOLD)
            s = pos_str[: w - col - 1]
            stdscr.addstr(row, col, s)
            col += len(s)
            stdscr.attroff(curses.A_BOLD)
            stdscr.attroff(curses.color_pair(6))

        if col < w:
            stdscr.attron(curses.color_pair(2))
            s = status_right[: w - col - 1]
            stdscr.addstr(row, col, s)
            stdscr.attroff(curses.color_pair(2))

        stdscr.refresh()

        if player.finished():
            break

        stdscr.timeout(50)
        ch = stdscr.getch()
        if ch == -1:
            continue

        if ch == 27:
            aborted = True
            break
        if ch in (ord("q"), ord("Q")):
            saving = True
            break

        if ch in (ord(" "), 10, 13):
            if current_index < num_lines:
                t = player.current_pos()
                timings.append(
                    {
                        "line_index": current_index,
                        "time": t,
                        "text": lyrics[current_index],
                    }
                )
                last_msg = f"Tagged line {current_index+1} at {fmt_time(t)}"
                current_index = min(current_index + 1, num_lines)
                if current_index >= num_lines:
                    saving = True
                    break
            continue

        if ch in NOTE_KEYS:
            t = player.current_pos()
            glyph = NOTE_KEYS[ch]
            timings.append(
                {
                    "line_index": -1,
                    "time": t,
                    "text": glyph,
                }
            )
            last_msg = f"Inserted {glyph} at {fmt_time(t)}"
            continue

        if ch in (ord("<"), curses.KEY_LEFT):
            pos_before = player.current_pos()
            if pos_before <= 0.1:
                last_msg = "Already at song start"
                continue
            snapshot = (pos_before, deepcopy(timings), current_index)
            history.append(snapshot)
            new_pos = max(0.0, pos_before - rewind_step)
            timings[:] = [t for t in timings if t["time"] <= new_pos]
            current_index = recompute_current_index(new_pos)
            player.start(new_pos)
            last_msg = f"Rewind to {fmt_time(new_pos)}, deleted timings in window"
            continue

        if ch in (ord("u"), ord("U")):
            if history:
                prev_pos, prev_timings, prev_idx = history.pop()
                timings[:] = prev_timings
                current_index = prev_idx
                player.start(prev_pos)
                last_msg = f"Undo to {fmt_time(prev_pos)}"
            else:
                last_msg = "Nothing to undo"
            continue

        if ch in (ord(">"), curses.KEY_RIGHT):
            pos_before = player.current_pos()
            new_pos = min(player.duration, pos_before + rewind_step)
            current_index = recompute_current_index(new_pos)
            player.start(new_pos)
            last_msg = f"Fast-forward to {fmt_time(new_pos)}"
            continue

        if ch in (ord("p"), ord("P")):
            if player.paused:
                player.pause_toggle()
                last_msg = f"Resumed at {fmt_time(player.current_pos())}"
            else:
                player.pause_toggle()
                last_msg = f"Paused at {fmt_time(player.current_pos())}"
            continue

        if ch in (ord("r"), ord("R")):
            pos_here = player.current_pos()
            pos_here_str = fmt_time(pos_here)
            if not prompt_yes_no(stdscr, "Restart song from 00:00? y/N: "):
                last_msg = "Restart cancelled"
                continue
            mode = prompt_clear_mode(stdscr, pos_here_str)
            if mode == 1:
                cleared = len(timings)
                timings[:] = []
                history.clear()
                last_msg = f"Restarted, cleared all {cleared} timings"
            elif mode == 2:
                before = len(timings)
                timings[:] = [t for t in timings if t["time"] > pos_here]
                cleared = before - len(timings)
                history.clear()
                last_msg = f"Restarted, cleared {cleared} timings ≤ {pos_here_str}"
            else:
                last_msg = "Restarted, kept timings"
            current_index = 0
            player.restart()
            continue

        if ch in (ord("g"), ord("G")):
            curses.echo()
            stdscr.timeout(-1)
            prompt = "Go to time (mm:ss or seconds): "
            stdscr.move(h - 2, 0)
            stdscr.clrtoeol()
            stdscr.attron(curses.color_pair(3))
            stdscr.addstr(h - 2, 0, prompt[: w - 1])
            stdscr.attroff(curses.color_pair(3))
            stdscr.refresh()
            try:
                s = stdscr.getstr(h - 2, len(prompt)).decode("utf-8")
            except Exception:
                s = ""
            curses.noecho()
            stdscr.timeout(50)
            if not s.strip():
                last_msg = "Goto cancelled"
                continue
            try:
                tgt = parse_time_string(s)
            except Exception:
                last_msg = "Invalid time format"
                continue
            tgt = max(0.0, min(player.duration, tgt))
            current_index = recompute_current_index(tgt)
            player.start(tgt)
            last_msg = f"Goto {fmt_time(tgt)}"
            continue

    return aborted, saving


def write_timings(timing_path: Path, lyrics, timings) -> None:
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    timings_sorted = sorted(timings, key=lambda t: t["time"])
    with timing_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "time_secs", "text"])
        for t in timings_sorted:
            idx = t.get("line_index", -1)
            sec = t["time"]
            if "text" in t and t["text"]:
                text = t["text"]
            else:
                text = lyrics[idx] if 0 <= idx < len(lyrics) else ""
            writer.writerow([idx, f"{sec:.3f}", text])


def parse_args(argv):
    p = argparse.ArgumentParser(description="Interactive timing editor.")
    p.add_argument("--txt", type=str, required=True, help="Lyrics txt path")
    p.add_argument("--audio", type=str, required=True, help="Audio file to play")
    p.add_argument("--timings", type=str, help="Timings CSV path")
    p.add_argument("--rewind-step", type=float, default=5.0, help="Seconds for < and >")
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
    history: list[tuple[float, list[dict], int]] = []

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

# end of 3_timing.py
