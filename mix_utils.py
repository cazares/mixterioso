#!/usr/bin/env python3
"""
Shared lightweight helpers for Mixterioso.

Rules:
- ONLY tiny reusable helpers
- NO heavy logic (no UI, no demucs logic, no ffmpeg logic)
- MUST be safe to import from ALL pipeline scripts
- MUST simplify scripts, never complicate them
"""

import json
import subprocess
import time
from pathlib import Path

from scripts.common import (
# ─────────────────────────────────────────────
# COLORS
# ─────────────────────────────────────────────
    RESET,
    CYAN,
    GREEN,
    YELLOW,
    RED,
    BLUE,
    WHITE,
    BOLD,
    DEFAULT_DEMUCS_MODEL
)

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

# ─────────────────────────────────────────────
# FATAL
# ─────────────────────────────────────────────
def fatal(msg: str, section="ERROR"):
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
# PATHS (authoritative)
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

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
# AUTO-CREATE PIPELINE DIRS
# ─────────────────────────────────────────────
def ensure_pipeline_dirs() -> None:
    for key, path in PATHS.items():
        if key in ("scripts", "base"):
            continue
        path.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
# FILE OVERWRITE CONFIRMATION
# ─────────────────────────────────────────────
def confirm_overwrite(path: Path, label="file") -> None:
    if not path.exists():
        return
    print(f"{YELLOW}WARNING: Overwriting existing {label}:{RESET}")
    print(f"   • {path}")
    if not ask_yes_no("Proceed?", default_yes=False):
        fatal("Cancelled to avoid overwriting.")

# ─────────────────────────────────────────────
# FIND MP3 CANDIDATES
# ─────────────────────────────────────────────
def find_mp3_candidates() -> list[Path]:
    mp3_dir = PATHS["mp3"]
    return sorted(mp3_dir.glob("*.mp3"))

# ─────────────────────────────────────────────
# CHOOSE MP3 (for stems)
# ─────────────────────────────────────────────
def choose_mp3() -> Path:
    mp3s = find_mp3_candidates()
    if not mp3s:
        fatal("No mp3 files found in mp3s/. Run Step 1 first.", "MP3")

    if len(mp3s) == 1:
        log("MP3", f"Using {mp3s[0].name}", GREEN)
        return mp3s[0]

    print(f"{CYAN}Multiple mp3s found:{RESET}")
    for i, p in enumerate(mp3s, start=1):
        print(f"  {i}) {p.name}")
    print()

    while True:
        try:
            raw = input("Choose mp3 number: ").strip()
        except EOFError:
            fatal("EOF received, no selection.")

        if raw.isdigit():
            i = int(raw)
            if 1 <= i <= len(mp3s):
                log("MP3", f"Selected {mp3s[i-1].name}", GREEN)
                return mp3s[i - 1]
        print("Invalid number.")

# ─────────────────────────────────────────────
# STEMS DIR UTIL
# ─────────────────────────────────────────────
def stems_dir(slug: str, model: str) -> Path:
    return PATHS["separated"] / model / slug

# ─────────────────────────────────────────────
# INSPECT EXISTING STEMS
# ─────────────────────────────────────────────
def inspect_stems(stem_path: Path, tracks=("vocals","bass","drums","other")):
    if not stem_path.exists():
        return "none", {}

    found = {}
    for t in tracks:
        p = stem_path / f"{t}.wav"
        if p.exists():
            found[t] = p

    if not found:
        return "none", {}
    if len(found) < len(tracks):
        return "partial", found
    return "all", found

# ─────────────────────────────────────────────
# RUN DEMUCS
# ─────────────────────────────────────────────

def run_demucs(mp3_path: Path, model: str = DEFAULT_DEMUCS_MODEL):
    cmd = ["demucs", "-n", model, str(mp3_path)]
    run_with_timer(cmd, "DEMUX", BLUE)

# ─────────────────────────────────────────────
# CLEAN EMPTY DIRS
# ─────────────────────────────────────────────
def clean_empty_dirs(root: Path):
    if not root.exists():
        return
    for p in sorted(root.rglob("*"), reverse=True):
        if p.is_dir() and not any(p.iterdir()):
            try:
                p.rmdir()
            except Exception:
                pass

# ─────────────────────────────────────────────
# FFPROBE DURATION
# ─────────────────────────────────────────────
def ffprobe_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
        ).strip()
        return float(out)
    except Exception:
        return 0.0

# ─────────────────────────────────────────────
# TIMER
# ─────────────────────────────────────────────
def run_with_timer(cmd: list[str], label: str, *, color=BLUE) -> float:
    log(label, " ".join(cmd), color)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0

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
# PIPELINE STATUS PRINTER
# ─────────────────────────────────────────────
def print_pipeline_status(slug: str, s1: bool, s2: bool, s3: bool, s4: bool) -> None:
    print()
    print(f"{BOLD}{CYAN}Pipeline status for '{slug}':{RESET}")
    print(f"  Step1 txt/mp3 : {'OK' if s1 else 'MISSING'}")
    print(f"  Step2 stems   : {'OK' if s2 else 'MISSING'}")
    print(f"  Step3 timing  : {'OK' if s3 else 'MISSING'}")
    print(f"  Step4 mp4     : {'OK' if s4 else 'MISSING'}")
    print()
# end of mix_utils.py
