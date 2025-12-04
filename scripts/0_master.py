#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mix_utils import (
    log, CYAN, GREEN, YELLOW, RED,
    slugify, ask_yes_no, print_pipeline_status,
    run_with_timer, PATHS,
)

TXT_DIR = PATHS["txt"]
MP3_DIR = PATHS["mp3"]
MIX_DIR = PATHS["mixes"]
TIM_DIR = PATHS["timings"]
OUT_DIR = PATHS["output"]
SCRIPTS = PATHS["scripts"]

# ─────────────────────────────────────────────
# STEP EXISTENCE CHECKS
# ─────────────────────────────────────────────
def exists_step1(slug: str) -> bool:
    return (TXT_DIR / f"{slug}.txt").exists() and (MP3_DIR / f"{slug}.mp3").exists()

def exists_step2(slug: str) -> bool:
    return (MIX_DIR / f"{slug}.wav").exists()

def exists_step3(slug: str) -> bool:
    return (TIM_DIR / f"{slug}.csv").exists()

def exists_step4(slug: str) -> bool:
    return (OUT_DIR / f"{slug}.mp4").exists()

# ─────────────────────────────────────────────
# RUN HELPERS
# ─────────────────────────────────────────────
def run_step1() -> float:
    """
    Step 1: txt + mp3 generation.
    1_txt_mp3.py now prompts for Artist/Title itself and does not take arguments.
    """
    cmd = [sys.executable, str(SCRIPTS / "1_txt_mp3.py")]
    return run_with_timer(cmd, "STEP1")

def run_step2(slug: str) -> float:
    """
    Step 2: stems + mix WAV.
    2_stems.py currently expects an explicit --mp3 argument.
    """
    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        raise SystemExit(f"Cannot run stems: mp3 missing at {mp3}")
    cmd = [sys.executable, str(SCRIPTS / "2_stems.py"), "--mp3", str(mp3)]
    return run_with_timer(cmd, "STEP2")

def run_step3(slug: str) -> float:
    """
    Step 3: manual timings.
    3_timing.py now handles slug selection and file paths internally (no args).
    """
    cmd = [sys.executable, str(SCRIPTS / "3_timing.py")]
    return run_with_timer(cmd, "STEP3")

def run_step4(slug: str) -> float:
    """
    Step 4: MP4 render.
    Keeps existing --slug contract for current 4_mp4.py.
    """
    cmd = [sys.executable, str(SCRIPTS / "4_mp4.py"), "--slug", slug]
    return run_with_timer(cmd, "STEP4")

# ─────────────────────────────────────────────
# SLUG PICKER
# ─────────────────────────────────────────────
def pick_slug() -> str:
    """
    Ask user for slug, or fall back to latest mp3 stem.
    """
    try:
        s = input("Enter slug (or ENTER for latest): ").strip()
    except EOFError:
        s = ""
    if s:
        return slugify(s)

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found. Provide slug or run step1.")
    return slugify(mp3s[-1].stem)

# ─────────────────────────────────────────────
# STEP SELECTION
# ─────────────────────────────────────────────
def ask_steps(slug: str) -> list[int]:
    s1 = exists_step1(slug)
    s2 = exists_step2(slug)
    s3 = exists_step3(slug)
    s4 = exists_step4(slug)

    print_pipeline_status(slug, s1, s2, s3, s4)

    fresh = not any([s1, s2, s3, s4])

    if fresh:
        if ask_yes_no("Run full pipeline 1→4 now?", default_yes=True):
            return [1, 2, 3, 4]

    try:
        raw = input("Enter steps (e.g. 134 or 24, 0=none): ").strip()
    except EOFError:
        raw = ""

    if raw == "" or raw == "0":
        return []

    steps = sorted({int(c) for c in raw if c in "1234"})
    return steps

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    slug = pick_slug()
    steps = ask_steps(slug)
    if not steps:
        log("MASTER", "No steps selected. Exiting.", YELLOW)
        return

    for step in steps:
        if step == 1:
            run_step1()
            # After Step 1, the new slug is determined by the newly created mp3.
            slug = pick_slug()
        elif step == 2:
            run_step2(slug)
        elif step == 3:
            run_step3(slug)
        elif step == 4:
            run_step4(slug)

    print()
    log("DONE", f"Pipeline complete for '{slug}'", GREEN)

if __name__ == "__main__":
    main()
# end of 0_master.py
