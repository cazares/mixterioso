#!/usr/bin/env python3
"""Mixterioso shared utilities.

Design goals:
- Single entrypoint: scripts/main.py
- Safe-by-default reuse of existing artifacts
- -f/--force overwrites without prompts
- -c/--confirm prompts before overwrite (TTY only)
- --dry-run prints actions without writing
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

# -----------------------------
# Logging
# -----------------------------
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
WHITE = "\033[37m"
BOLD = "\033[1m"


def log(tag: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}", flush=True)

DEFAULT_DEMUCS_MODEL = "mdx_extra_q"

# -----------------------------
# Paths
# -----------------------------
@dataclass(frozen=True)
class Paths:
    root: Path
    scripts: Path
    txts: Path
    mp3s: Path
    mixes: Path
    separated: Path
    timings: Path
    output: Path
    meta: Path
    cache: Path

    @staticmethod
    def from_scripts_dir(scripts_path: Path) -> "Paths":
        """Build paths from a scripts directory OR a file inside scripts/."""
        scripts_dir = scripts_path
        if scripts_dir.suffix:  # file path
            scripts_dir = scripts_dir.parent
        scripts_dir = scripts_dir.resolve()
        root = scripts_dir.parent
        return Paths(
            root=root,
            scripts=scripts_dir,
            txts=root / "txts",
            mp3s=root / "mp3s",
            mixes=root / "mixes",
            separated=root / "separated",
            timings=root / "timings",
            output=root / "output",
            meta=root / "meta",
            cache=root / ".cache" / "mixterioso",
        )

    def ensure(self) -> None:
        """Create all expected directories."""
        for d in [
            self.txts,
            self.mp3s,
            self.mixes,
            self.separated,
            self.timings,
            self.output,
            self.meta,
            self.cache,
        ]:
            d.mkdir(parents=True, exist_ok=True)

# -----------------------------
# Flags
# -----------------------------
@dataclass(frozen=True)
class IOFlags:
    force: bool = False
    confirm: bool = False
    dry_run: bool = False


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


def should_write(path: Path, flags: IOFlags, *, label: str) -> bool:
    """Return True if we should create/overwrite path.

    Safe default:
    - If missing: write
    - If exists: reuse unless flags.force or (flags.confirm and user confirms)
    """
    if flags.dry_run:
        # Caller can still decide to "would write".
        return not path.exists()

    if not path.exists():
        return True

    if flags.force:
        return True

    if flags.confirm and _is_tty():
        while True:
            ans = input(f"Overwrite {label}? {path} [y/N]: ").strip().lower()
            if ans in ("y", "yes"):
                return True
            if ans in ("", "n", "no"):
                return False

    return False


def ensure_dirs(paths: Paths, flags: IOFlags) -> None:
    for d in (paths.txts, paths.mp3s, paths.mixes, paths.separated, paths.timings, paths.output, paths.meta, paths.cache):
        if flags.dry_run:
            continue
        d.mkdir(parents=True, exist_ok=True)


# -----------------------------
# Query parsing / slugify
# -----------------------------
_DASHES = [" - ", " — ", " – ", " — "]


def parse_query(query: str) -> Tuple[str, str]:
    """Parse a user query into (artist, title).

    Accepts:
    - "Artist - Title"
    - "Artist — Title" (em dash / en dash)
    - If no delimiter, returns ("", query)
    """
    q = (query or "").strip()
    if not q:
        return "", ""

    for sep in _DASHES:
        if sep in q:
            a, t = q.split(sep, 1)
            return a.strip(), t.strip()

    # Heuristic: last hyphen with spaces around it
    m = re.search(r"\s-\s", q)
    if m:
        a, t = q.split(" - ", 1)
        return a.strip(), t.strip()

    return "", q



def slugify(title: str) -> str:
    s = (title or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s or "song"


# -----------------------------
# IO helpers
# -----------------------------
def write_text(path: Path, text: str, flags: IOFlags, *, label: str) -> None:
    if not should_write(path, flags, label=label):
        log(label.upper(), f"Reusing existing: {path}", YELLOW)
        return
    if flags.dry_run:
        log(label.upper(), f"[dry-run] Would write {path}", BLUE)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    log(label.upper(), f"Wrote {path}", GREEN)


def write_json(path: Path, obj: Any, flags: IOFlags, *, label: str) -> None:
    if not should_write(path, flags, label=label):
        log(label.upper(), f"Reusing existing: {path}", YELLOW)
        return
    if flags.dry_run:
        log(label.upper(), f"[dry-run] Would write {path}", BLUE)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(label.upper(), f"Wrote {path}", GREEN)


def write_csv_rows(path: Path, rows: List[Tuple[int, float, str]], flags: IOFlags, *, label: str) -> None:
    if not should_write(path, flags, label=label):
        log(label.upper(), f"Reusing existing: {path}", YELLOW)
        return
    if flags.dry_run:
        log(label.upper(), f"[dry-run] Would write {path} ({len(rows)} rows)", BLUE)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "time_secs", "text"])
        for li, t, txt in rows:
            w.writerow([li, f"{t:.3f}", txt])
    log(label.upper(), f"Wrote {path} ({len(rows)} rows)", GREEN)


def have_exe(name: str) -> bool:
    return shutil.which(name) is not None


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    tag: str = "CMD",
    dry_run: bool = False,
) -> int:
    """
    Run a subprocess and let it stream output directly to the console (no buffering surprises).

    We intentionally *do not* capture stdout/stderr here. Tools like yt-dlp and ffmpeg
    behave much better (progress bars, live logs) when they inherit the parent TTY.
    """
    if dry_run:
        log(tag, "DRY-RUN: " + " ".join(map(str, cmd)), YELLOW)
        return 0

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    # Encourage unbuffered logs for Python-based tools when possible.
    merged_env.setdefault("PYTHONUNBUFFERED", "1")

    log(tag, "RUN: " + " ".join(map(str, cmd)), CYAN)
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged_env)
    except FileNotFoundError:
        log(tag, f"Command not found: {cmd[0]}", RED)
        return 127
    except Exception as e:
        log(tag, f"Failed to run command: {e}", RED)
        return 1

    if r.returncode != 0:
        log(tag, f"Command exited {r.returncode}", RED)
    return r.returncode



def ffprobe_duration_secs(path: Path) -> float:
    if not path.exists():
        return 0.0
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip()
        return float(out)
    except Exception:
        return 0.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def write_csv(path: Path, header: list[str], rows: list[list[str]], flags: IOFlags, *, label: str) -> None:
    """Write CSV safely with the overwrite/confirm contract."""
    if not should_write(path, flags, label=label):
        return
    if flags.dry_run:
        log('DRYRUN', f"Would write CSV: {path}", YELLOW)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    log(label.upper(), f"Wrote {path}", GREEN)


# end of common.py
