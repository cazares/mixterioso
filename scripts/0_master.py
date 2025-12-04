#!/usr/bin/env python3
"""
0_master.py — Orchestrator for Mixterioso Karaoke Pipeline.

Major rules:
- NO free-form slug/query at startup.
- Step1 (1_txt_mp3.py) is the ONLY place a NEW slug is created.
- For existing songs (when Step1 is not run), user picks a slug from a filtered list.
- Slug is constant for Steps 2–5 once chosen/created.
- Offset is only asked at Step4, default = –1.50 seconds.
"""

import subprocess
import sys
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

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
    BASE_DIR = Path(__file__).resolve().parent.parent
    PATHS = {
        "base": BASE_DIR,
        "scripts": BASE_DIR / "scripts",
        "txt": BASE_DIR / "txts",
        "mp3": BASE_DIR / "mp3s",
        "mixes": BASE_DIR / "mixes",
        "timings": BASE_DIR / "timings",
        "output": BASE_DIR / "output",
    }

    def slugify(text: str) -> str:
        s = text.lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        s = re.sub(r"_+", "_", s)
        return s.strip("_")

BASE_DIR     = PATHS["base"]
SCRIPTS_DIR  = PATHS["scripts"]
TXT_DIR      = PATHS["txt"]
MP3_DIR      = PATHS["mp3"]
MIXES_DIR    = PATHS["mixes"]
TIMINGS_DIR  = PATHS["timings"]
OUTPUT_DIR   = PATHS["output"]
PYTHON_BIN   = sys.executable

# ==========================================================
# SLUG DISCOVERY
# ==========================================================
def _stems_from_dir(d: Path, exts: Sequence[str]) -> Set[str]:
    out: Set[str] = set()
    if d.exists():
        for ext in exts:
            for p in d.glob(f"*{ext}"):
                if p.is_file():
                    out.add(p.stem)
    return out

def collect_existing_slugs() -> List[str]:
    slugs: Set[str] = set()
    slugs |= _stems_from_dir(TXT_DIR, [".txt"])
    slugs |= _stems_from_dir(MP3_DIR, [".mp3"])
    slugs |= _stems_from_dir(MIXES_DIR, [".wav", ".mp3"])
    slugs |= _stems_from_dir(TIMINGS_DIR, [".csv"])
    slugs |= _stems_from_dir(OUTPUT_DIR, [".mp4", ".mkv"])
    return sorted(slugs)

# ==========================================================
# PIPELINE STATUS
# ==========================================================
def step1_ready(slug: str) -> bool:
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
# EXISTING SONG SELECTION (NO NEW SONG CREATION HERE)
# ==========================================================
def choose_existing_slug(existing_slugs: List[str]) -> str:
    """
    Existing-only selection (used when Step1 is NOT requested).
    Optional filter text, then numeric choice. No 'new song' option here.
    """
    if not existing_slugs:
        log("SLUG", "No existing songs found; Step1 is required to create a new one.", RED)
        raise SystemExit(1)

    while True:
        print("")
        log("SONGS", f"{len(existing_slugs)} existing song(s) available.", WHITE)
        flt = input("Filter songs (optional; ENTER to list all): ").strip().lower()

        if flt:
            songs = [s for s in existing_slugs if flt in s.lower()]
            if not songs:
                log("SLUG", f"No songs match filter '{flt}'. Try again.", YELLOW)
                continue
        else:
            songs = existing_slugs

        print("")
        log("SONGS", "Matching songs:", CYAN)
        for i, s in enumerate(songs, 1):
            print(f"  {i:3d}) {s}")
        print("")
        choice = input(f"Choose 1–{len(songs)} (0=filter again, q=quit): ").strip().lower()

        if choice == "q":
            raise SystemExit(0)
        if choice == "0":
            continue
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(songs):
                slug = songs[n - 1]
                log("SLUG", f"Using existing slug '{slug}'", GREEN)
                return slug

        log("SLUG", "Invalid selection, please try again.", YELLOW)

# ==========================================================
# STEP EXECUTION HELPERS
# ==========================================================
def run_subprocess(step: int, args: Sequence[str]) -> int:
    cmd = [PYTHON_BIN] + list(args)
    log(f"STEP{step}", " ".join(str(x) for x in cmd), GREEN)
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log(f"STEP{step}", f"Exited {r.returncode}", RED)
    return r.returncode

def run_step1(current_slug: Optional[str]) -> Optional[str]:
    """
    Run 1_txt_mp3.py, then detect which slug was created/updated.

    - If current_slug is already present after Step1, keep it.
    - Else if exactly one new slug appeared, use it.
    - Else ask user to choose from all slugs.
    """
    before = set(collect_existing_slugs())
    rc = run_subprocess(1, [str(SCRIPTS_DIR / "1_txt_mp3.py")])
    if rc != 0:
        return None

    after = set(collect_existing_slugs())
    new = sorted(after - before)

    if current_slug and current_slug in after:
        log("STEP1", f"Continuing with slug '{current_slug}'", GREEN)
        return current_slug

    if len(new) == 1:
        chosen = new[0]
        log("STEP1", f"Detected new slug '{chosen}'", GREEN)
        return chosen

    candidates = sorted(after)
    if not candidates:
        log("STEP1", "No slugs found after Step1; cannot continue.", RED)
        return None

    print("")
    log("STEP1", "Select which slug Step1 produced:", YELLOW)
    for i, s in enumerate(candidates, 1):
        print(f"  {i}) {s}")
    print("")

    while True:
        c = input(f"Choose 1–{len(candidates)} (0=abort): ").strip()
        if c == "0":
            return None
        if c.isdigit():
            n = int(c)
            if 1 <= n <= len(candidates):
                return candidates[n - 1]
        print("Invalid selection, try again.")

def run_step2(slug: str) -> None:
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log("STEP2", f"Missing MP3 for slug '{slug}' at {mp3_path}", RED)
        raise SystemExit(1)
    run_subprocess(2, [str(SCRIPTS_DIR / "2_stems.py"), "--mp3", str(mp3_path)])

def run_step3(slug: str) -> None:
    run_subprocess(3, [str(SCRIPTS_DIR / "3_timing.py"), "--slug", slug])

def prompt_for_offset() -> float:
    print("")
    log("OFFSET", "MP4 render timing offset", WHITE)
    print("  Positive → lyrics later / delayed")
    print("  Negative → lyrics earlier")
    print("  Default = –1.50s")
    print("")
    raw = input("Offset seconds [default=-1.50]: ").strip()
    if not raw:
        return -1.50
    try:
        return float(raw)
    except Exception:
        log("OFFSET", "Invalid input; using -1.50s", YELLOW)
        return -1.50

def run_step4(slug: str) -> None:
    offset = prompt_for_offset()
    run_subprocess(4, [str(SCRIPTS_DIR / "4_mp4.py"), "--slug", slug, "--offset", str(offset)])

def run_step5(slug: str) -> None:
    run_subprocess(5, [str(SCRIPTS_DIR / "5_upload.py"), "--slug", slug])

# ==========================================================
# MAIN
# ==========================================================
def normalize_steps(raw: str) -> List[int]:
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
    return steps

def main() -> None:
    existing_slugs = collect_existing_slugs()

    print("")
    log("MIXTERIOSO", "Welcome to Mixterioso", BOLD + BLUE)
    print("")
    log("SONGS", f"{len(existing_slugs)} existing song(s) detected.", WHITE)
    print("")
    print("Available Steps:")
    print("  1) TXT/MP3      – Fetch lyrics + download MP3 (creates/updates slug)")
    print("  2) STEMS        – Demucs stem extraction + mix")
    print("  3) TIMING       – Manual lyric timing (curses)")
    print("  4) MP4 RENDER   – Create karaoke video (offset applied here)")
    print("  5) UPLOAD       – YouTube uploader")
    print("")
    raw = input("Select steps to run (e.g. 1345; 0=none): ").strip()
    steps = normalize_steps(raw)

    if not steps:
        log("MAIN", "No steps selected. Exiting.", YELLOW)
        return

    log("MAIN", f"Running steps: {''.join(str(s) for s in steps)}", WHITE)

    pipeline_slug: Optional[str] = None

    # If Step1 is NOT requested, we must choose an existing slug up front.
    if 1 not in steps:
        pipeline_slug = choose_existing_slug(existing_slugs)
        print("")
        print_status(pipeline_slug)

    # Execute steps in order.
    for step in steps:
        if step == 1:
            pipeline_slug = run_step1(pipeline_slug)
            if not pipeline_slug:
                log("MAIN", "Step1 failed or aborted; stopping.", RED)
                return
            print("")
            print_status(pipeline_slug)

        elif step == 2:
            if not pipeline_slug:
                log("STEP2", "No slug available. Step1 or existing selection required.", RED)
                return
            run_step2(pipeline_slug)

        elif step == 3:
            if not pipeline_slug:
                log("STEP3", "No slug available. Step1 or existing selection required.", RED)
                return
            run_step3(pipeline_slug)

        elif step == 4:
            if not pipeline_slug:
                log("STEP4", "No slug available. Step1 or existing selection required.", RED)
                return
            run_step4(pipeline_slug)

        elif step == 5:
            if not pipeline_slug:
                log("STEP5", "No slug available. Step1 or existing selection required.", RED)
                return
            run_step5(pipeline_slug)

    log("MAIN", "Pipeline finished.", GREEN)

if __name__ == "__main__":
    main()

# end of 0_master.py
