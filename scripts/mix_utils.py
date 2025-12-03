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
# ----------------------------------------------------------
# ADDITIVE GENERIC HELPERS (SAFE TO IMPORT ANYWHERE)
# ----------------------------------------------------------

def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    """
    A unified yes/no prompt.
    - default_yes=True → ENTER counts as 'yes'
    - default_yes=False → ENTER counts as 'no'
    Returns True for yes, False for no.
    """
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"{prompt} {suffix}: ").strip().lower()
    except EOFError:
        ans = ""

    if ans == "" and default_yes:
        return True
    if ans == "" and not default_yes:
        return False
    return ans in ("y", "yes")


def print_pipeline_status(slug: str, s1: bool, s2: bool, s3: bool, s4: bool) -> None:
    """
    Identical formatted pipeline-state print for ANY runner.
    Used optionally by 0_master.py or anything else.
    """
    print()
    print(f"{BOLD}{CYAN}Pipeline status for '{slug}':{RESET}")
    print(f"  Step1 txt/mp3 : {'OK' if s1 else 'MISSING'}")
    print(f"  Step2 stems   : {'OK' if s2 else 'MISSING'}")
    print(f"  Step3 timing  : {'OK' if s3 else 'MISSING'}")
    print(f"  Step4 mp4     : {'OK' if s4 else 'MISSING'}")
    print()


# Centralized path dictionary (read-only convenience)
from pathlib import Path as _Path

BASE_DIR = _Path(__file__).resolve().parent.parent
PATHS = {
    "base":      BASE_DIR,
    "scripts":   BASE_DIR / "scripts",
    "txt":       BASE_DIR / "txts",
    "mp3":       BASE_DIR / "mp3s",
    "mixes":     BASE_DIR / "mixes",
    "timings":   BASE_DIR / "timings",
    "output":    BASE_DIR / "output",
    "separated": BASE_DIR / "separated",
    "meta":      BASE_DIR / "meta",
}
