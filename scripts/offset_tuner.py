#!/usr/bin/env python3
"""Interactive offset tuner (CLI-only).

Intent:
- No MP4 preview (final video only)
- Simple terminal UI with 0.25s steps, unlimited retries
- Preview spans multiple lyric "borders" (line start moments), even if offset is off by several seconds
- On lock: write timings/<slug>.offset

Preview strategy:
- Play an audio segment near the first lyric (duration ~25–40s)
- While audio plays, emit terminal bell + print each lyric line at the moment it would appear (time_secs + offset)
- During preview you can stop or adjust immediately:
    Enter        -> stop preview and return to menu
    1 + Enter    -> Earlier (-0.25s) and immediately replay preview
    2 + Enter    -> Later   (+0.25s) and immediately replay preview
    4 + Enter    -> lock offset immediately
    5/q + Enter  -> abort
"""

from __future__ import annotations

import sys
import time
import csv
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple, Optional

try:
    import select  # POSIX (macOS)
except Exception:
    select = None  # type: ignore

# common.py varies across iterations; be defensive about optional symbols.
try:
    from .common import log, YELLOW, GREEN, RED, BLUE  # type: ignore
except Exception:
    from .common import log  # type: ignore
    YELLOW = GREEN = RED = BLUE = ""

STEP = 0.25

# Preview window selection
LEAD_SECS = 6.0
TAIL_SECS = 6.0
MIN_DUR = 25.0
MAX_DUR = 40.0

MAX_LINES_TO_PRINT = 14
PLAY_START_PAD = 0.10


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def _read_offset_file(offset_path: Path) -> Optional[float]:
    try:
        if not offset_path.exists():
            return None
        raw = offset_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return float(raw)
    except Exception:
        return None


def _write_offset_file(offset_path: Path, offset: float) -> None:
    offset_path.parent.mkdir(parents=True, exist_ok=True)
    offset_path.write_text(f"{offset:.2f}\n", encoding="utf-8")


def _find_audio_path(mixes_dir: Path, slug: str) -> Path:
    candidates = [
        mixes_dir / f"{slug}.wav",
        mixes_dir / f"{slug}.mp3",
        mixes_dir / f"{slug}.m4a",
        mixes_dir / f"{slug}.aac",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"No audio found for slug '{slug}' in {mixes_dir} (tried wav/mp3/m4a/aac)"
    )


def _load_timings_csv(csv_path: Path) -> List[Tuple[float, str]]:
    rows: List[Tuple[float, str]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                t = float((r.get("time_secs") or "").strip())
            except Exception:
                continue
            txt = (r.get("text") or "").strip()
            rows.append((t, txt))
    rows.sort(key=lambda x: x[0])
    return rows


def _choose_preview_window(events: List[Tuple[float, str]]) -> Tuple[float, float]:
    if not events:
        return 0.0, MIN_DUR

    t0 = events[0][0]
    start = max(0.0, t0 - LEAD_SECS)

    # Ensure we cross multiple borders early on
    target_end = t0 + MIN_DUR
    for idx in range(min(8, len(events))):
        target_end = max(target_end, events[idx][0] + TAIL_SECS)

    dur = max(MIN_DUR, target_end - start)
    dur = min(dur, MAX_DUR)

    return start, dur


def _build_schedule(
    events: List[Tuple[float, str]],
    *,
    preview_start: float,
    preview_end: float,
    offset: float,
) -> List[Tuple[float, str]]:
    sched: List[Tuple[float, str]] = []
    for (t_abs, txt) in events:
        t_show = t_abs + offset
        if preview_start <= t_show <= preview_end:
            sched.append((t_show - preview_start, txt))
    sched.sort(key=lambda x: x[0])
    return sched


def _terminate_proc(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
    except Exception:
        return
    try:
        proc.wait(timeout=0.5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _play_with_ffplay(audio_path: Path, *, start: float, dur: float) -> subprocess.Popen:
    ffplay = shutil.which("ffplay")
    if not ffplay:
        raise RuntimeError("ffplay not found")
    cmd = [
        ffplay,
        "-hide_banner",
        "-loglevel", "error",
        "-nodisp",
        "-autoexit",
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        str(audio_path),
    ]
    # Prevent ffplay from consuming terminal stdin
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ensure_audio_clip_ffmpeg(audio_path: Path, cache_dir: Path, *, start: float, dur: float) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"{audio_path.stem}_preview_{int(start*1000)}_{int(dur*1000)}.wav"
    out = cache_dir / key
    if out.exists():
        return out

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found (needed to slice a short preview clip when ffplay is unavailable)")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-ss", f"{start:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(audio_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "48000",
        "-ac", "2",
        str(out),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def _play_with_afplay(file_path: Path) -> subprocess.Popen:
    afplay = shutil.which("afplay")
    if not afplay:
        raise RuntimeError("afplay not found")
    cmd = [afplay, "-q", str(file_path)]
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _read_preview_command_nonblocking() -> Optional[str]:
    """Return a line if the user pressed Enter, else None."""
    if select is None:
        return None
    try:
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if not r:
            return None
        line = sys.stdin.readline()
        if line is None:
            return None
        return line.strip()
    except Exception:
        return None


def _preview(
    audio_path: Path,
    events: List[Tuple[float, str]],
    *,
    timings_dir: Path,
    preview_start: float,
    preview_dur: float,
    offset: float,
) -> str:
    """Run preview. Returns one of:
    DONE, STOP, EARLIER, LATER, LOCK, ABORT
    """
    preview_end = preview_start + preview_dur
    sched = _build_schedule(events, preview_start=preview_start, preview_end=preview_end, offset=offset)

    prior = [txt for (t_abs, txt) in events if (t_abs + offset) < preview_start]
    if prior:
        log("PREVIEW", f"On-screen at start: {prior[-1]}", BLUE)

    try:
        proc = _play_with_ffplay(audio_path, start=preview_start, dur=preview_dur)
        using = "ffplay"
    except Exception:
        cache_dir = timings_dir.parent / ".cache" / "mixterioso" / "previews"
        clip = _ensure_audio_clip_ffmpeg(audio_path, cache_dir, start=preview_start, dur=preview_dur)
        proc = _play_with_afplay(clip)
        using = "ffmpeg+afplay"

    log("PREVIEW", f"start={preview_start:.2f}s dur={preview_dur:.2f}s offset={offset:+.2f}s player={using}", BLUE)
    log("PREVIEW", "Preview controls: Enter=stop, 1=earlier, 2=later, 4=lock, 5/q=abort", BLUE)

    def map_cmd(cmd: str) -> str:
        c = (cmd or "").strip()
        if not c:
            return "STOP"
        ch = c[0]
        if ch == "1":
            return "EARLIER"
        if ch == "2":
            return "LATER"
        if ch == "4":
            return "LOCK"
        if ch == "5" or ch.lower() in ("q", "a"):
            return "ABORT"
        return "STOP"

    t0 = time.time() + PLAY_START_PAD
    printed = 0
    i = 0

    try:
        while True:
            if proc.poll() is not None:
                return "DONE"

            cmd = _read_preview_command_nonblocking()
            if cmd is not None:
                action = map_cmd(cmd)
                _terminate_proc(proc)
                return action

            if i >= len(sched) or printed >= MAX_LINES_TO_PRINT:
                time.sleep(0.05)
                continue

            t_rel, txt = sched[i]
            elapsed = time.time() - t0
            dt = t_rel - elapsed
            if dt > 0:
                time.sleep(min(dt, 0.05))
                continue

            sys.stdout.write("\a")
            sys.stdout.flush()
            log("LYRIC", f"+{t_rel:6.2f}s  {txt}")
            printed += 1
            i += 1

    except KeyboardInterrupt:
        _terminate_proc(proc)
        log("PREVIEW", "Cancelled", YELLOW)
        return "STOP"


def tune_offset(
    *,
    slug: str,
    base_offset: float,
    mixes_dir: Path,
    timings_dir: Path,
    renderer_path=None,  # unused (kept for call compatibility)
) -> float:
    if not _is_tty():
        raise RuntimeError("--confirm-offset requires an interactive TTY")

    csv_path = timings_dir / f"{slug}.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Timings CSV not found: {csv_path}")

    offset_path = timings_dir / f"{slug}.offset"
    existing = _read_offset_file(offset_path)
    offset = existing if existing is not None else float(base_offset)

    audio_path = _find_audio_path(mixes_dir, slug)
    events = _load_timings_csv(csv_path)
    if not events:
        raise RuntimeError(f"Timings CSV has no usable rows: {csv_path}")

    preview_start, preview_dur = _choose_preview_window(events)

    while True:
        print()
        print("----------------------------------------")
        print(f"Slug: {slug}")
        print(f"Current offset: {offset:+.2f}s")
        print()
        print("[1] Earlier (-0.25s)")
        print("[2] Later   (+0.25s)")
        print("[3] Play preview (audio + terminal border cues)")
        print("[4] Lock offset and continue")
        print("[5] Abort")
        choice = (input("> ") or "").strip().lower()

        if choice in ("1", "e", "earlier"):
            offset -= STEP
        elif choice in ("2", "l", "later"):
            offset += STEP
        elif choice in ("3", "p", "preview"):
            # Allow on-the-fly adjust during preview
            while True:
                result = _preview(
                    audio_path,
                    events,
                    timings_dir=timings_dir,
                    preview_start=preview_start,
                    preview_dur=preview_dur,
                    offset=offset,
                )
                if result == "EARLIER":
                    offset -= STEP
                    continue  # immediate replay
                if result == "LATER":
                    offset += STEP
                    continue  # immediate replay
                if result == "LOCK":
                    _write_offset_file(offset_path, offset)
                    log("OFFSET", f"Locked {offset:+.2f}s -> {offset_path}", GREEN)
                    return offset
                if result == "ABORT":
                    raise SystemExit(1)
                break  # STOP/DONE -> back to menu
        elif choice in ("4", "lock", "continue"):
            _write_offset_file(offset_path, offset)
            log("OFFSET", f"Locked {offset:+.2f}s -> {offset_path}", GREEN)
            return offset
        elif choice in ("5", "q", "quit", "abort"):
            raise SystemExit(1)
        else:
            log("REVIEW", "Invalid input", YELLOW)

# end of offset_tuner.py
