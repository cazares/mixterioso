#!/usr/bin/env python3
# scripts/0_master.py

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# COLORS
# ============================================================================
RESET   = "\033[0m"
BOLD    = "\033[1m"
WHITE   = "\033[97m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"

def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")

# ============================================================================
# PATHS
# ============================================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR     = BASE_DIR / "txts"
MP3_DIR     = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
OFFSETS_DIR = BASE_DIR / "offsets"
MIXES_DIR   = BASE_DIR / "mixes"
META_DIR    = BASE_DIR / "meta"
OUTPUT_DIR  = BASE_DIR / "output"
UPLOAD_LOG  = BASE_DIR / "uploaded"

# ============================================================================
# Helpers
# ============================================================================
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

def fmt_secs(sec: float) -> str:
    m = int(sec // 60)
    s = int(sec - m * 60)
    return f"{m:02d}:{s:02d}"

def format_offset_tag(offset: float) -> str:
    sign = "p" if offset >= 0 else "m"
    v = abs(offset)
    sec_int = int(v)
    ms_int  = int(round((v - sec_int) * 1000))
    return f"{sign}{sec_int}p{ms_int:03d}s"

def detect_latest_slug() -> str | None:
    if not META_DIR.exists():
        return None
    files = sorted(
        META_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    return files[0].stem if files else None

def get_meta_title_for_slug(slug: str) -> str:
    meta_path = META_DIR / f"{slug}.json"
    if not meta_path.exists():
        return slug.replace("_", " ")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        artist = (meta.get("artist") or "").strip()
        title  = (meta.get("title")  or slug.replace("_", " ")).strip()
        if artist and title:
            return f"{title} by {artist}"
        return title
    except Exception:
        return slug.replace("_", " ")

# ============================================================================
# Step Status — FULLY OFFSET-AWARE
# ============================================================================
def detect_step_status(slug: str, profile: str) -> dict[str, str]:
    status = {"slug": slug, "profile": profile}

    mp3 = MP3_DIR / f"{slug}.mp3"
    txt = TXT_DIR / f"{slug}.txt"
    meta = META_DIR / f"{slug}.json"
    status["1"] = "DONE" if (mp3.exists() and txt.exists() and meta.exists()) else "MISSING"

    mix = MIXES_DIR / f"{slug}_{profile}.wav"
    status["2"] = "DONE" if mix.exists() else "MISSING"

    csv = TIMINGS_DIR / f"{slug}.csv"
    status["3"] = "DONE" if csv.exists() else "MISSING"

    outputs = list(OUTPUT_DIR.glob(f"{slug}_{profile}_offset_*.mp4"))
    status["4"] = "DONE" if outputs else "MISSING"

    if UPLOAD_LOG.exists() and any(UPLOAD_LOG.glob(f"{slug}_{profile}_offset_*.json")):
        status["5"] = "DONE"
    else:
        status["5"] = "MISSING"

    return status

# ============================================================================
# Utilities
# ============================================================================
def prompt_yes_no(msg: str, default_yes=True) -> bool:
    default = "Y/n" if default_yes else "y/N"
    while True:
        ans = input(f"{msg} [{default}]: ").lower().strip()
        if ans == "" and default_yes:
            return True
        if ans == "" and not default_yes:
            return False
        if ans in ("y","yes"): return True
        if ans in ("n","no"):  return False
        print(f"{RED}Please answer Y or N.{RESET}")

def run(cmd: list[str], section: str) -> float:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    return time.perf_counter() - t0

def run_capture(cmd: list[str], section: str) -> tuple[float, str]:
    log(section, " ".join(cmd), BLUE)
    t0 = time.perf_counter()
    cp = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return (time.perf_counter() - t0, cp.stdout)

def read_offset(slug: str) -> float:
    p = OFFSETS_DIR / f"{slug}.txt"
    if not p.exists():
        return 0.0
    try: return float(p.read_text().strip())
    except Exception: return 0.0

def write_offset(slug: str, offset: float) -> None:
    OFFSETS_DIR.mkdir(parents=True, exist_ok=True)
    (OFFSETS_DIR / f"{slug}.txt").write_text(f"{offset:.3f}")
