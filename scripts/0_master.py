#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path

RESET = "\033[0m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
BLUE  = "\033[34m"

def log(sec, msg, color=CYAN):
    print(f"{color}[{sec}]{RESET} {msg}")

BASE = Path(__file__).resolve().parent.parent
S = BASE / "scripts"

TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
MIX_DIR = BASE / "mixes"
TIM_DIR = BASE / "timings"
OUT_DIR = BASE / "output"

def slugify(t: str) -> str:
    import re
    t = t.lower().strip()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^\w\-]+", "", t)
    return t or "song"

def exists_step1(slug):
    return (
        (TXT_DIR / f"{slug}.txt").exists() and
        (MP3_DIR / f"{slug}.mp3").exists()
    )

def exists_step2(slug):
    return (MIX_DIR / f"{slug}.wav").exists()

def exists_step3(slug):
    return (TIM_DIR / f"{slug}.csv").exists()

def exists_step4(slug):
    return (OUT_DIR / f"{slug}.mp4").exists()

def run(cmd, name):
    log(name, " ".join(cmd), BLUE)
    subprocess.run(cmd, check=True)

def pick_slug():
    try:
        s = input("Enter slug (or blank to auto-detect latest): ").strip()
    except EOFError:
        s = ""
    if s:
        return slugify(s)

    mp3s = sorted(MP3_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not mp3s:
        raise SystemExit("No mp3s found. Provide slug or run step1.")
    return slugify(mp3s[-1].stem)

def ask_steps(slug):
    s1 = exists_step1(slug)
    s2 = exists_step2(slug)
    s3 = exists_step3(slug)
    s4 = exists_step4(slug)

    fresh = not any([s1, s2, s3, s4])

    print()
    print(f"Detected pipeline state for slug '{slug}':")
    print(f"  Step1 txt/mp3 : {'OK' if s1 else 'MISSING'}")
    print(f"  Step2 stems   : {'OK' if s2 else 'MISSING'}")
    print(f"  Step3 timing  : {'OK' if s3 else 'MISSING'}")
    print(f"  Step4 mp4     : {'OK' if s4 else 'MISSING'}")
    print()

    if fresh:
        try:
            ans = input("Run full pipeline 1→4 now? [Y/n]: ").strip().lower()
        except EOFError:
            ans = "y"
        if ans in ("", "y", "yes"):
            return [1,2,3,4]

    try:
        raw = input("Enter steps (e.g. 134 or 24, 0=none): ").strip()
    except EOFError:
        raw = ""
    if not raw or raw == "0":
        return []

    steps = sorted({int(c) for c in raw if c in "1234"})
    return steps

def run_step1(query: str):
    if not query:
        raise SystemExit("Step 1 requires a search query.")
    run([sys.executable, str(S / "1_txt_mp3.py"), query], "STEP1")

def run_step2(slug: str):
    mp3 = MP3_DIR / f"{slug}.mp3"
    if not mp3.exists():
        raise SystemExit("Cannot run stems: mp3 missing. Run step1 first.")
    run([sys.executable, str(S / "2_stems.py"), "--mp3", str(mp3)], "STEP2")

def run_step3(slug: str):
    txt = TXT_DIR / f"{slug}.txt"
    aud = MP3_DIR / f"{slug}.mp3"
    csv = TIM_DIR / f"{slug}.csv"
    run([
        sys.executable, str(S / "3_timing.py"),
        "--txt", str(txt),
        "--audio", str(aud),
        "--timings", str(csv),
    ], "STEP3")

def run_step4(slug: str):
    run([
        sys.executable, str(S / "4_mp4.py"),
        "--slug", slug
    ], "STEP4")

def main():
    slug = pick_slug()

    # Ask step 1 query only IF they choose step 1.
    steps = ask_steps(slug)
    if not steps:
        log("MASTER", "No steps selected. Exiting.", YELLOW)
        return

    query = ""
    if 1 in steps:
        try:
            q = input("Enter search query for Step1: ").strip()
        except EOFError:
            q = ""
        if not q:
            raise SystemExit("Step1 chosen but no query given.")
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
    print(f"{GREEN}Pipeline complete for slug '{slug}'.{RESET}")

if __name__ == "__main__":
    main()

# end of 0_master.py
