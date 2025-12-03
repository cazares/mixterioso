#!/usr/bin/env python3
"""
Minimal shared helpers for the Mixterioso pipeline.

This module centralizes ONLY lightweight, duplicated utilities:
- ANSI colors
- logging
- fatal + overwrite confirmations
- slugify
- directory paths
- mp3 discovery + chooser
- demucs helpers
- stem inspection
- JSON helpers
- timers
- empty-dir cleanup

NO heavy logic, NO curses UI, NO ffmpeg composition.
"""

import os
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
# LOGGING + FATAL
# ─────────────────────────────────────────────
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def fatal(msg: str, section: str = "ERROR") -> None:
    log(section, msg, RED)
    raise SystemExit(msg)


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
# ASK YES/NO
# ─────────────────────────────────────────────
def ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
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


# ─────────────────────────────────────────────
# CONFIRM OVERWRITE
# ─────────────────────────────────────────────
def confirm_overwrite(path: Path, label="file"):
    if not path.exists():
        return
    print()
    print(f"{YELLOW}WARNING: About to overwrite {label}:{RESET}")
    print(f"   • {path}")
    print()
    if not ask_yes_no("Overwrite?", default_yes=False):
        fatal("Cancelled to avoid overwriting.", section="ABORT")


# ─────────────────────────────────────────────
# BASE PATHS
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent

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


# ─────────────────────────────────────────────
# DEMUCS MODEL CONSTANT
# ─────────────────────────────────────────────
DEFAULT_DEMUCS_MODEL = "htdemucs"


# ─────────────────────────────────────────────
# MP3 DISCOVERY / CHOOSER
# ─────────────────────────────────────────────
def find_mp3_candidates() -> list[Path]:
    mp3_dir = PATHS["mp3"]
    mp3_dir.mkdir(exist_ok=True)
    return sorted(p for p in mp3_dir.glob("*.mp3") if p.is_file())


def choose_mp3_interactive(candidates: list[Path]) -> Path:
    print(f"{CYAN}Multiple mp3s found:{RESET}")
    for i, p in enumerate(candidates, start=1):
        print(f"  {i}) {p.name}")
    print()

    while True:
        try:
            raw = input("Choose mp3 number: ").strip()
        except EOFError:
            fatal("No choice made (EOF).", "MP3")

        if not raw.isdigit():
            print("Enter a number.")
            continue

        idx = int(raw)
        if 1 <= idx <= len(candidates):
            mp3 = candidates[idx - 1]
            log("MP3", f"Selected {mp3.name}", GREEN)
            return mp3

        print("Invalid number.")


def choose_mp3() -> Path:
    candidates = find_mp3_candidates()
    if not candidates:
        fatal("No mp3s found. Run step 1 first.", "MP3")
    if len(candidates) == 1:
        mp3 = candidates[0]
        log("MP3", f"Using {mp3.name}", GREEN)
        return mp3
    return choose_mp3_interactive(candidates)


# ─────────────────────────────────────────────
# STEMS DIR + INSPECTION
# ─────────────────────────────────────────────
def stems_dir(slug: str, model: str = DEFAULT_DEMUCS_MODEL) -> Path:
    return PATHS["separated"] / model / slug


def inspect_stems(stems_dir: Path, tracks: list[str]) -> tuple[str, dict[str, Path]]:
    mapping = {t: stems_dir / f"{t}.wav" for t in tracks}
    existing_count = sum(1 for p in mapping.values() if p.exists())

    if existing_count == 0:
        return "none", mapping
    if existing_count == len(tracks):
        return "all", mapping
    return "partial", mapping


# ─────────────────────────────────────────────
# RUN DEMUCS
# ─────────────────────────────────────────────
def run_with_timer(cmd: list[str], label: str, *, color=BLUE) -> float:
    log(label, " ".join(cmd), color)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0


def run_demucs(mp3_path: Path, model: str = DEFAULT_DEMUCS_MODEL) -> float:
    if not mp3_path.exists():
        fatal(f"MP3 not found: {mp3_path}", "DEMUX")

    log("DEMUX", f"Extracting stems with model={model}", BLUE)
    cmd = ["demucs", "-n", model, str(mp3_path)]
    elapsed = run_with_timer(cmd, "DEMUX", color=BLUE)
    log("DEMUX", f"Finished extracting stems in {elapsed:.1f}s.", GREEN)
    return elapsed


# ─────────────────────────────────────────────
# CLEAN EMPTY DIRS
# ─────────────────────────────────────────────
def clean_empty_dirs(root: Path):
    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        p = Path(dirpath)
        if p == root:
            continue
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except Exception:
            pass


# ─────────────────────────────────────────────
# JSON HELPERS
# ─────────────────────────────────────────────
def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────
# PIPELINE STATUS (unchanged)
# ─────────────────────────────────────────────
def print_pipeline_status(slug: str, s1: bool, s2: bool, s3: bool, s4: bool) -> None:
    print()
    print(f"{BOLD}{CYAN}Pipeline status for '{slug}':{RESET}")
    print(f"  Step1 txt/mp3 : {'OK' if s1 else 'MISSING'}")
    print(f"  Step2 stems   : {'OK' if s2 else 'MISSING'}")
    print(f"  Step3 timing  : {'OK' if s3 else 'MISSING'}")
    print(f"  Step4 mp4     : {'OK' if s4 else 'MISSING'}")
    print()
