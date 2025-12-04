#!/usr/bin/env python3
"""
0_master.py — Orchestrator for Mixterioso Karaoke Pipeline.

Strict rules (V1 locking):
- Artist + Title asked once at startup.
- Slug = slugify(title), NO fuzzy logic.
- Step1 "existing" only if BOTH txt + mp3 are present (E2).
- Step1 overwrite requires explicit user confirmation (O2).
- Steps 2–5 NEVER ask for slug, never guess mp3, never use "latest".
- Each step receives ONLY the arguments its script actually supports.
"""

import subprocess
import sys
from pathlib import Path
from typing import Dict, List

# ==========================================================
# COLORS / LOGGING
# ==========================================================
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
WHITE  = "\033[97m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


# ==========================================================
# PATHS / SLUGIFY
# ==========================================================
try:
    from mix_utils import PATHS, slugify  # type: ignore
except Exception:
    import re

    BASE_DIR = Path(__file__).resolve().parent.parent
    PATHS = {
        "base": BASE_DIR,
        "scripts": BASE_DIR / "scripts",
        "txts": BASE_DIR / "txts",
        "mp3s": BASE_DIR / "mp3s",
        "mixes": BASE_DIR / "mixes",
        "timings": BASE_DIR / "timings",
        "output": BASE_DIR / "output",
    }

    def slugify(text: str) -> str:
        s = text.lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s)
        return s.strip("_")

BASE_DIR: Path = PATHS["base"]
SCRIPTS_DIR: Path = PATHS["scripts"]
TXT_DIR: Path = PATHS.get("txts", PATHS.get("txt", BASE_DIR / "txts"))
MP3_DIR: Path = PATHS.get("mp3s", PATHS.get("mp3", BASE_DIR / "mp3s"))
MIXES_DIR: Path = PATHS.get("mixes", BASE_DIR / "mixes")
TIMINGS_DIR: Path = PATHS.get("timings", BASE_DIR / "timings")
OUTPUT_DIR: Path = PATHS.get("output", BASE_DIR / "output")

PYTHON_BIN = sys.executable


# ==========================================================
# STEP READINESS
# ==========================================================
def step1_ready(slug: str) -> bool:
    """Existing only if BOTH txt + mp3 exist (E2)."""
    return (TXT_DIR / f"{slug}.txt").exists() and (MP3_DIR / f"{slug}.mp3").exists()


def step2_ready(slug: str) -> bool:
    if (MIXES_DIR / f"{slug}.wav").exists():
        return True
    for _ in MIXES_DIR.glob(f"{slug}_*.wav"):
        return True
    for _ in MIXES_DIR.glob(f"{slug}_*.mp3"):
        return True
    return False


def step3_ready(slug: str) -> bool:
    return (TIMINGS_DIR / f"{slug}.csv").exists()


def step4_ready(slug: str) -> bool:
    for _ in OUTPUT_DIR.glob(f"{slug}*.mp4"):
        return True
    for _ in OUTPUT_DIR.glob(f"{slug}*.mkv"):
        return True
    return False


def compute_status(slug: str) -> Dict[int, str]:
    return {
        1: "READY" if step1_ready(slug) else "MISSING",
        2: "READY" if step2_ready(slug) else "MISSING",
        3: "READY" if step3_ready(slug) else "MISSING",
        4: "READY" if step4_ready(slug) else "MISSING",
        5: "READY",
    }


def print_status(slug: str) -> None:
    st = compute_status(slug)
    log("STATUS", f"Pipeline status for '{slug}':", BOLD + WHITE)
    print(f"  1 txt/mp3 : {st[1]}")
    print(f"  2 stems   : {st[2]}")
    print(f"  3 timing  : {st[3]}")
    print(f"  4 mp4     : {st[4]}")
    print(f"  5 upload  : {st[5]}")
    print("")


# ==========================================================
# ARGUMENT CONTRACTS (V1: exact lockdown)
# ==========================================================
ALLOWED_FLAGS = {
    1: {"--artist", "--title", "--slug"},
    2: {"--mp3", "--model"},
    3: {"--slug"},
    4: {"--slug", "--offset"},
    5: {"--slug"},
}

REQUIRED_FLAGS = {
    1: {"--artist", "--title", "--slug"},
    2: {"--mp3"},
    3: {"--slug"},
    4: {"--slug", "--offset"},
    5: {"--slug"},
}


def _extract_flags(args: List[str]) -> List[str]:
    """Extract all tokens that look like flags (start with --)."""
    flags: List[str] = []
    for tok in args:
        if tok.startswith("--"):
            flags.append(tok)
    return flags


def validate_step_args(step: int, args: List[str]) -> None:
    """
    V1: Exact argument lockdown.
    - No unsupported flags.
    - No missing required flags.
    - No duplicate flags.
    """
    if step not in ALLOWED_FLAGS:
        log("ARGS", f"Unknown step {step} for validation.", RED)
        raise SystemExit(1)

    allowed = ALLOWED_FLAGS[step]
    required = REQUIRED_FLAGS[step]

    # args is like [script_path, --flag, value, ...]
    # We validate ONLY the flags themselves.
    flags = _extract_flags(args[1:])

    # Duplicate flags
    seen = set()
    for fl in flags:
        if fl in seen:
            log("ARGS", f"Duplicate flag {fl} for step {step}.", RED)
            raise SystemExit(1)
        seen.add(fl)

    # Unsupported flags
    for fl in flags:
        if fl not in allowed:
            log("ARGS", f"Unsupported flag {fl} for step {step}. Allowed: {sorted(allowed)}", RED)
            raise SystemExit(1)

    # Missing required flags
    missing = [fl for fl in required if fl not in flags]
    if missing:
        log("ARGS", f"Missing required flag(s) for step {step}: {missing}", RED)
        raise SystemExit(1)


# ==========================================================
# ARTIST + TITLE → STRICT SLUG
# ==========================================================
def prompt_artist_title_slug() -> tuple[str, str, str, bool]:
    print("")
    log("MIXTERIOSO", "Welcome to Mixterioso", BOLD + BLUE)
    print("")
    print("We need the Artist and Title. Slug derives strictly from Title.")
    print("")

    try:
        artist = input("Artist: ").strip()
        title = input("Title: ").strip()
    except EOFError:
        raise SystemExit("Missing Artist or Title (EOF)")

    if not artist or not title:
        raise SystemExit("Artist and Title are required.")

    slug = slugify(title)
    log("SLUG", f'Canonical slug = "{slug}"', GREEN)

    is_new = not step1_ready(slug)
    if is_new:
        log("SONG", f"No txt/mp3 found for '{slug}'. NEW song.", YELLOW)
    else:
        log("SONG", f"Existing txt/mp3 detected for '{slug}'.", WHITE)

    return artist, title, slug, is_new


# ==========================================================
# STEP RUNNERS
# ==========================================================
def run_subprocess(step: int, args: List[str]) -> int:
    """
    args: [script_path, flag, value, ...] (no python executable)
    """
    validate_step_args(step, args)
    cmd = [PYTHON_BIN] + args
    log(f"STEP{step}", " ".join(cmd), GREEN)
    p = subprocess.run(cmd)
    if p.returncode != 0:
        log(f"STEP{step}", f"Exited with code {p.returncode}", RED)
    else:
        log(f"STEP{step}", "Completed successfully.", GREEN)
    return p.returncode


def run_step1(slug: str, artist: str, title: str) -> bool:
    txt = TXT_DIR / f"{slug}.txt"
    mp3 = MP3_DIR / f"{slug}.mp3"

    if step1_ready(slug):
        print("")
        log("STEP1", "TXT/MP3 already exist:", WHITE)
        print(f"  TXT: {txt}")
        print(f"  MP3: {mp3}")
        resp = input("Overwrite existing TXT/MP3? [y/N]: ").strip().lower()
        if resp not in ("y", "yes"):
            log("STEP1", "Skipping overwrite, using existing files.", YELLOW)
            return True

    args = [
        str(SCRIPTS_DIR / "1_txt_mp3.py"),
        "--artist", artist,
        "--title", title,
        "--slug", slug,
    ]
    if run_subprocess(1, args) != 0:
        return False

    if not step1_ready(slug):
        log("STEP1", "TXT/MP3 incomplete after Step1. Aborting.", RED)
        return False

    return True


def run_step2(slug: str) -> None:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log("STEP2", f"Missing MP3 for slug '{slug}' at {mp3_path}", RED)
        raise SystemExit(1)

    args = [
        str(SCRIPTS_DIR / "2_stems.py"),
        "--mp3", str(mp3_path),
    ]
    run_subprocess(2, args)


def run_step3(slug: str) -> None:
    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("STEP3", f"Missing TXT for slug '{slug}' at {txt_path}", RED)
        raise SystemExit(1)

    args = [
        str(SCRIPTS_DIR / "3_timing.py"),
        "--slug", slug,
    ]
    run_subprocess(3, args)


def prompt_for_offset() -> float:
    print("")
    log("OFFSET", "MP4 render timing offset", WHITE)
    print("Positive = lyrics later, Negative = earlier")
    raw = input("Offset seconds [default=0]: ").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except Exception:
        log("OFFSET", "Invalid offset. Using 0.0", YELLOW)
        return 0.0


def run_step4(slug: str, offset: float) -> None:
    # Soft dependency checks (do not hard-stop, just warn if missing)
    if not step2_ready(slug):
        log("STEP4", "Warning: no stems/mixes detected. MP4 may still render using raw mp3.", YELLOW)
    if not step3_ready(slug):
        log("STEP4", "Warning: no timings CSV detected. Lyrics may not appear.", YELLOW)

    args = [
        str(SCRIPTS_DIR / "4_mp4.py"),
        "--slug", slug,
        "--offset", str(offset),
    ]
    run_subprocess(4, args)


def run_step5(slug: str) -> None:
    if not step4_ready(slug):
        log("STEP5", "Warning: no MP4 detected for this slug. Upload may fail.", YELLOW)

    args = [
        str(SCRIPTS_DIR / "5_upload.py"),
        "--slug", slug,
    ]
    run_subprocess(5, args)


# ==========================================================
# MAIN FLOW
# ==========================================================
def normalize_steps(raw: str, is_new: bool) -> List[int]:
    raw = raw.strip()
    if not raw or raw == "0":
        return []
    steps: List[int] = []
    for ch in raw:
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= 5 and n not in steps:
                steps.append(n)
    steps.sort()
    if is_new and 1 not in steps:
        log("STEPS", "NEW song requires Step1 → auto-adding 1", YELLOW)
        steps.insert(0, 1)
    return steps


def main() -> None:
    artist, title, slug, is_new = prompt_artist_title_slug()

    if step1_ready(slug):
        print("")
        print_status(slug)
    else:
        print("")
        log("STATUS", "Step1 will create txt/mp3 for this slug.", WHITE)
        print("")

    print("Available Steps:")
    print("  1) TXT/MP3      – Fetch lyrics + download MP3")
    print("  2) STEMS        – Demucs stem extraction + mix")
    print("  3) TIMING       – Manual lyric timing tool")
    print("  4) MP4 RENDER   – Create karaoke video")
    print("  5) UPLOAD       – YouTube uploader")
    print("")
    steps_raw = input("Select steps (e.g. 1345; 0=none): ").strip()
    steps = normalize_steps(steps_raw, is_new)

    if not steps:
        log("MAIN", "No steps selected. Exiting.", YELLOW)
        return

    log("MAIN", f"Running steps: {''.join(str(s) for s in steps)}", WHITE)

    offset = 0.0
    if 4 in steps:
        offset = prompt_for_offset()

    for s in steps:
        if s == 1:
            if not run_step1(slug, artist, title):
                log("MAIN", "Step1 failed. Aborting.", RED)
                return
            print("")
            print_status(slug)

        elif s == 2:
            run_step2(slug)

        elif s == 3:
            run_step3(slug)

        elif s == 4:
            run_step4(slug, offset)

        elif s == 5:
            run_step5(slug)

    log("MAIN", "Pipeline complete.", GREEN)


if __name__ == "__main__":
    main()

# end of 0_master.py
