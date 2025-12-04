#!/usr/bin/env python3
"""
Master pipeline runner for Mixterioso.

Steps:
  1 = txt/mp3 generation (artist+title → lyrics + mp3)
  2 = stems extraction + mix
  3 = manual timings (CSV)
  4 = mp4 rendering
  5 = YouTube upload

No automagic. User chooses exactly which steps run.
"""

import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path
# ─────────────────────────────────────────────
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
META_DIR = PATHS["meta"]
SCRIPTS = PATHS["scripts"]


# ─────────────────────────────────────────────
# Existence checks
# ─────────────────────────────────────────────
def exists_step1(slug): return (TXT_DIR / f"{slug}.txt").exists() and (MP3_DIR / f"{slug}.mp3").exists()
def exists_step2(slug): return (MIX_DIR / f"{slug}.wav").exists()
def exists_step3(slug): return (TIM_DIR / f"{slug}.csv").exists()
def exists_step4(slug): return (OUT_DIR / f"{slug}.mp4").exists()
def exists_step5(slug): return True  # always runnable if step4 exists


# ─────────────────────────────────────────────
# Step runners
# ─────────────────────────────────────────────
def run_step1():
    cmd = [sys.executable, str(SCRIPTS / "1_txt_mp3.py")]
    return run_with_timer(cmd, "STEP1")

def run_step2(slug):
    cmd = [sys.executable, str(SCRIPTS / "2_stems.py"), "--mp3", str(MP3_DIR / f"{slug}.mp3")]
    return run_with_timer(cmd, "STEP2")

def run_step3(slug):
    cmd = [
        sys.executable, str(SCRIPTS / "3_timing.py"),
        "--txt", str(TXT_DIR / f"{slug}.txt"),
        "--audio", str(MP3_DIR / f"{slug}.mp3"),
        "--timings", str(TIM_DIR / f"{slug}.csv"),
    ]
    return run_with_timer(cmd, "STEP3")

def run_step4(slug):
    cmd = [sys.executable, str(SCRIPTS / "4_mp4.py"), "--slug", slug]
    return run_with_timer(cmd, "STEP4")

def run_step5(slug):
    cmd = [sys.executable, str(SCRIPTS / "5_upload.py"), "--slug", slug]
    return run_with_timer(cmd, "STEP5")


# ─────────────────────────────────────────────
# Slug selection
# ─────────────────────────────────────────────
def pick_slug():
    try:
        s = input("Enter slug (or ENTER for latest mp3): ").strip()
    except EOFError:
        s = ""

    if s:
        return slugify(s)

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found. Create one via Step 1.")
    return slugify(mp3s[-1].stem)


# ─────────────────────────────────────────────
# Choose steps
# ─────────────────────────────────────────────
def ask_steps(slug: str) -> list[int]:
    s1 = exists_step1(slug)
    s2 = exists_step2(slug)
    s3 = exists_step3(slug)
    s4 = exists_step4(slug)

    print_pipeline_status(slug, s1, s2, s3, s4)

    fresh = not any([s1, s2, s3, s4])
    if fresh:
        if ask_yes_no("Run full pipeline 1→5 now?", default_yes=True):
            return [1,2,3,4,5]

    try:
        raw = input("Enter steps (e.g. 124 or 35; 0=none): ").strip()
    except EOFError:
        raw = ""

    if raw in ("", "0"):
        return []

    steps = sorted({int(c) for c in raw if c in "12345"})
    return steps


# ─────────────────────────────────────────────
# Main
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
            slug = pick_slug()
        elif step == 2:
            run_step2(slug)
        elif step == 3:
            run_step3(slug)
        elif step == 4:
            run_step4(slug)
        elif step == 5:
            if not exists_step4(slug):
                log("ERROR", "Step 4 must be completed before uploading (Step 5).", RED)
                break
            run_step5(slug)

    print()
    log("DONE", f"Pipeline complete for '{slug}'", GREEN)


if __name__ == "__main__":
    main()

# end of 0_master.py
