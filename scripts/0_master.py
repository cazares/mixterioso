#!/usr/bin/env python3
import sys
from pathlib import Path

from scripts.mix_utils import (
    log, CYAN, GREEN, YELLOW,
    slugify, ask_yes_no, print_pipeline_status,
    run_with_timer, PATHS, fatal
)

# Directories
TXT_DIR = PATHS["txt"]
MP3_DIR = PATHS["mp3"]
MIX_DIR = PATHS["mixes"]
TIM_DIR = PATHS["timings"]
OUT_DIR = PATHS["output"]
SCRIPTS = PATHS["scripts"]


# STEP EXISTENCE CHECKS
def exists_step1(slug: str) -> bool:
    return (
        (TXT_DIR / f"{slug}.txt").exists()
        and (MP3_DIR / f"{slug}.mp3").exists()
    )

def exists_step2(slug: str) -> bool:
    return (MIX_DIR / f"{slug}.wav").exists()

def exists_step3(slug: str) -> bool:
    return (TIM_DIR / f"{slug}.csv").exists()

def exists_step4(slug: str) -> bool:
    return (OUT_DIR / f"{slug}.mp4").exists()


# RUN HELPERS
def run_step1(query: str) -> float:
    if not query:
        fatal("Step 1 requires a query.", "STEP1")
    cmd = [sys.executable, str(SCRIPTS / "1_txt_mp3.py"), query]
    return run_with_timer(cmd, "STEP1", color=CYAN)

def run_step2(slug: str) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        fatal("Cannot run stems: mp3 missing.", "STEP2")
    cmd = [sys.executable, str(SCRIPTS / "2_stems.py")]
    return run_with_timer(cmd, "STEP2", color=CYAN)

def run_step3(slug: str) -> float:
    txt = TXT_DIR / f"{slug}.txt"
    aud = MP3_DIR / f"{slug}.mp3"
    csv = TIM_DIR / f"{slug}.csv"
    cmd = [
        sys.executable, str(SCRIPTS / "3_timing.py"),
        "--txt", str(txt),
        "--audio", str(aud),
        "--timings", str(csv),
    ]
    return run_with_timer(cmd, "STEP3", color=CYAN)

def run_step4(slug: str) -> float:
    cmd = [
        sys.executable, str(SCRIPTS / "4_mp4.py"),
        "--slug", slug,
    ]
    return run_with_timer(cmd, "STEP4", color=CYAN)


# SLUG PICKER
def pick_slug() -> str:
    try:
        s = input("Enter slug (or ENTER for latest): ").strip()
    except EOFError:
        s = ""

    if s:
        return slugify(s)

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        fatal("No mp3s found. Provide slug or run step1.", "MASTER")
    return slugify(mp3s[-1].stem)


# STEP SELECTION
def ask_steps(slug: str):
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
        return []

    if not raw or raw == "0":
        return []

    return sorted({int(c) for c in raw if c in "1234"})


# MAIN
def main():
    slug = pick_slug()
    steps = ask_steps(slug)

    if not steps:
        log("MASTER", "No steps selected. Exiting.", YELLOW)
        return

    query = None
    if 1 in steps:
        try:
            q = input("Enter search query for Step 1: ").strip()
        except EOFError:
            fatal("No query.", "MASTER")
        if not q:
            fatal("Step 1 chosen but no query provided.", "MASTER")
        query = q

    for step in steps:
        if step == 1:
            run_step1(query)
            slug = slugify(query)
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
