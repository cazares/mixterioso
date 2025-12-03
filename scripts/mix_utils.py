#!/usr/bin/env python3
"""
Minimal shared helpers for the Mixterioso pipeline.

Goal:
Centralize lightweight, duplicated utilities:
- ANSI colors
- logging helper
- slugify
- directory helpers
- ffprobe duration
- pretty relative paths
- run_with_timer wrapper
- small JSON read/write helpers

NO ffmpeg logic, NO mix logic, NO stem logic here
(other modules handle the heavy stuff).
"""

import json
import subprocess
import time
from pathlib import Path

# ─────────────────────────────────────────────
# ANSI COLORS
# ─────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"


# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ─────────────────────────────────────────────
# SLUGIFY
# ─────────────────────────────────────────────
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# ─────────────────────────────────────────────
# DIRECTORY HELPERS
# ─────────────────────────────────────────────
def ensure_dir(path: Path) -> Path:
    """
    Ensures a directory exists and returns the Path.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─────────────────────────────────────────────
# PATH PRETTY PRINT
# ─────────────────────────────────────────────
def pretty_relpath(p: Path, base: Path) -> str:
    """
    Return a path relative to base/, prefixed with "./" if possible.
    Used for cleaner logs and meta printing.
    """
    try:
        return f"./{p.relative_to(base)}"
    except Exception:
        return str(p)


# ─────────────────────────────────────────────
# FFPROBE DURATION
# ─────────────────────────────────────────────
def ffprobe_duration(path: Path) -> float:
    """
    Returns duration in seconds using ffprobe.
    Returns 0.0 on error.
    """
    if not path.exists():
        return 0.0
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path)
            ],
            text=True
        ).strip()
        return float(out)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# RUN WITH TIMER
# ─────────────────────────────────────────────
def run_with_timer(cmd: list[str], label: str, *, color=BLUE) -> float:
    """
    Runs a subprocess, logs it, and returns elapsed seconds.
    """
    log(label, " ".join(cmd), color)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0


# ─────────────────────────────────────────────
# JSON HELPERS
# ─────────────────────────────────────────────
def read_json(path: Path) -> dict | None:
    """
    Safe JSON load: returns dict or None.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def write_json(path: Path, data: dict) -> None:
    """
    Safe JSON write with indentation.
    """
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
