#!/usr/bin/env python3
# scripts/4_merge.py
#
# STEP 4: WHISPERX AUTO-TIMING WRAPPER
# -------------------------------------
# - Calls _whisperx_align_driver.py
# - Streams logs so 0_master.py never looks "hung"
# - Always prints a final JSON dict on last line:
#       { "ok": true/false, "slug": "...", "csv": "...", ... }
#
# CSV schema produced:
#       line_index,start,end,text

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RESET  = "\033[0m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"

BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
TIMINGS_DIR = BASE / "timings"
TIMINGS_DIR.mkdir(exist_ok=True)

def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)

def run_whisperx(slug, language, passthrough):
    section = "WhisperX"

    txt = TXT_DIR / f"{slug}.txt"
    mp3 = MP3_DIR / f"{slug}.mp3"
    out_csv = TIMINGS_DIR / f"{slug}.csv"

    if not txt.exists():
        log(section, f"ERROR: lyrics file missing: {txt}", RED)
        return {"ok": False, "error": "lyrics-not-found"}
    if not mp3.exists():
        log(section, f"ERROR: mp3 missing: {mp3}", RED)
        return {"ok": False, "error": "mp3-not-found"}

    cmd = [
        "python3", "scripts/_whisperx_align_driver.py",
        "--audio", str(mp3),
        "--lyrics", str(txt),
        "--output", str(out_csv),
        "--language", language,
    ] + passthrough

    log(section, "RUN on " + str(mp3), CYAN)
    log(section, "CMD: " + " ".join(cmd), BLUE)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in proc.stdout:
        print(f"{CYAN}[WhisperX]{RESET} {line.rstrip()}", flush=True)

    proc.wait()
    rc = proc.returncode

    if rc != 0:
        log(section, f"FAILED (exit={rc})", RED)
        return {"ok": False, "error": "whisperx-failed"}

    if not out_csv.exists():
        log(section, "ERROR: expected CSV missing", RED)
        return {"ok": False, "error": "no-csv"}

    log(section, f"CSV READY → {out_csv}", GREEN)
    return {"ok": True, "slug": slug, "csv": str(out_csv)}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--language", default="en")
    p.add_argument("--pass", dest="passthrough", nargs="*", default=[])
    p.add_argument("extra", nargs="*", help="passthrough args")
    args = p.parse_args()

    passthrough = (args.passthrough or []) + (args.extra or [])

    result = run_whisperx(args.slug, args.language, passthrough)
    print(json.dumps(result))

if __name__ == "__main__":
    main()

# end of 4_merge.py
