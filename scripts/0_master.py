#!/usr/bin/env python3
import sys
from pathlib import Path
import argparse
import subprocess

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

TXT_DIR  = PATHS["txt"]
MP3_DIR  = PATHS["mp3"]
MIX_DIR  = PATHS["mixes"]
TIM_DIR  = PATHS["timings"]
OUT_DIR  = PATHS["output"]
META_DIR = PATHS["meta"]
SCRIPTS  = PATHS["scripts"]

# ─────────────────────────────────────────────
# Step existence checks (1–4 only)
# Step 5 is always READY
# ─────────────────────────────────────────────
def exists_step1(slug: str) -> bool:
    return (TXT_DIR / f"{slug}.txt").exists() and (MP3_DIR / f"{slug}.mp3").exists()

def exists_step2(slug: str) -> bool:
    return (MIX_DIR / f"{slug}.wav").exists()

def exists_step3(slug: str) -> bool:
    return (TIM_DIR / f"{slug}.csv").exists()

def exists_step4(slug: str) -> bool:
    return (OUT_DIR / f"{slug}.mp4").exists()

def exists_step5(slug: str) -> bool:
    # Upload leaves no file artifact → always READY
    return True

# ─────────────────────────────────────────────
# Step runners
# ─────────────────────────────────────────────
def run_step1() -> float:
    cmd = [sys.executable, str(SCRIPTS / "1_txt_mp3.py")]
    return run_with_timer(cmd, "STEP1")

def run_step2(slug: str) -> float:
    mp3 = MP3_DIR / f"{slug}.mp3"
    cmd = [sys.executable, str(SCRIPTS / "2_stems.py"), "--mp3", str(mp3)]
    return run_with_timer(cmd, "STEP2")

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
    return run_with_timer(cmd, "STEP3")

def run_step4(slug: str, offset: float) -> float:
    cmd = [sys.executable, str(SCRIPTS / "4_mp4.py"), "--slug", slug]
    # Only pass offset flag if non-zero, to keep CLI minimal
    if abs(offset) > 1e-6:
        cmd += ["--offset", str(offset)]
    log("STEP4", f"Invoking 4_mp4.py with offset {offset:+.3f}s", CYAN)
    return run_with_timer(cmd, "STEP4")

def run_step5(slug: str) -> float:
    cmd = [sys.executable, str(SCRIPTS / "5_upload.py"), "--slug", slug]
    return run_with_timer(cmd, "STEP5")

# ─────────────────────────────────────────────
# Slug picker
# ─────────────────────────────────────────────
def pick_slug() -> str:
    try:
        s = input("Enter slug (or ENTER for latest mp3): ").strip()
    except EOFError:
        s = ""
    if s:
        return slugify(s)

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found. Run step1 first.")
    return slugify(mp3s[-1].stem)

# ─────────────────────────────────────────────
# Step selector
# ─────────────────────────────────────────────
def ask_steps(slug: str) -> list[int]:
    s1 = exists_step1(slug)
    s2 = exists_step2(slug)
    s3 = exists_step3(slug)
    s4 = exists_step4(slug)
    s5 = exists_step5(slug)

    print()
    print(f"{CYAN}Pipeline status for '{slug}':{YELLOW}")
    print(f"  1 txt/mp3 : {'OK' if s1 else 'MISSING'}")
    print(f"  2 stems   : {'OK' if s2 else 'MISSING'}")
    print(f"  3 timing  : {'OK' if s3 else 'MISSING'}")
    print(f"  4 mp4     : {'OK' if s4 else 'MISSING'}")
    print(f"  5 upload  : READY{CYAN}")
    print()

    try:
        raw = input("Enter steps (e.g. 1345 or 45; 0=none): ").strip()
    except EOFError:
        raw = ""

    if raw == "" or raw == "0":
        return []

    steps = sorted({int(c) for c in raw if c in "12345"})
    return steps

# ─────────────────────────────────────────────
# CLI parsing
# ─────────────────────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Mixterioso pipeline master (steps 1–5)."
    )
    p.add_argument(
        "--offset",
        type=float,
        default=0.0,
        help="Lyrics offset in seconds for step 4 "
             "(positive = lyrics later, negative = earlier).",
    )
    return p.parse_args(argv or sys.argv[1:])

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main(argv=None):
    args = parse_args(argv)
    offset = args.offset

    slug = pick_slug()
    steps = ask_steps(slug)
    if not steps:
        log("MASTER", "No steps selected. Exiting.", YELLOW)
        return

    for step in steps:
        if step == 1:
            run_step1()
            # refresh slug based on new txt/mp3
            slug = pick_slug()
        elif step == 2:
            run_step2(slug)
        elif step == 3:
            run_step3(slug)
        elif step == 4:
            run_step4(slug, offset)

            # After rendering MP4 → offer to open folder and/or upload
            out_file = OUT_DIR / f"{slug}.mp4"
            print()
            if ask_yes_no("Open output directory now?", default_yes=False):
                subprocess.run(["open", str(OUT_DIR)])

            if ask_yes_no("Upload to YouTube now?", default_yes=False):
                run_step5(slug)

        elif step == 5:
            run_step5(slug)

    print()
    log("DONE", f"Pipeline complete for '{slug}'", GREEN)

if __name__ == "__main__":
    main()
# end of 0_master.py
