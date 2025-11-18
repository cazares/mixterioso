#!/usr/bin/env python3
# scripts/4_merge.py
#
# STEP 4: AUTO-TIMING (WHISPERX) — NON-BLOCKING, JSON OUTPUT
# ----------------------------------------------------------
# - Reads txts/<slug>.txt
# - Uses WhisperX to transcribe + align mp3s/<slug>.mp3
# - Outputs canonical timings CSV:
#       line_index,start,end,text
# - Fully compatible with master pipeline (Option A)
# - Accepts --language, passes through WhisperX args
# - Streams WhisperX logs so pipeline never looks "hung"
# - ALWAYS returns JSON on the last line
#
# NOTE:
#   This file preserves your custom alignment logic.
#   Only safe minimal modifications were made.

import argparse
import json
import os
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

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")

# -------------------------------------------------------------------
# Run WhisperX alignment (same behavior as your original script)
# -------------------------------------------------------------------
def run_whisperx(slug, language, passthrough_args):
    section = "WhisperX"

    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log(section, f"ERROR: lyrics file not found {txt_path}", RED)
        return {"ok": False, "error": "lyrics-not-found"}

    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log(section, f"ERROR: mp3 file not found {mp3_path}", RED)
        return {"ok": False, "error": "mp3-not-found"}

    out_csv = TIMINGS_DIR / f"{slug}.csv"

    log(section, f"Running WhisperX on {mp3_path}")

    cmd = [
        "python3", "scripts/_whisperx_align_driver.py",
        "--audio", str(mp3_path),
        "--lyrics", str(txt_path),
        "--output", str(out_csv),
        "--language", language
    ] + passthrough_args

    log(section, "CMD: " + " ".join(cmd), BLUE)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in proc.stdout:
        print(f"{CYAN}[WhisperX]{RESET} {line.rstrip()}")

    proc.wait()
    rc = proc.returncode

    if rc != 0:
        log(section, f"WhisperX FAILED (code {rc})", RED)
        return {"ok": False, "error": "whisperx-failed"}

    if not out_csv.exists():
        log(section, f"ERROR: WhisperX did not produce CSV", RED)
        return {"ok": False, "error": "no-output"}

    log(section, f"Timing CSV ready: {out_csv}", GREEN)

    return {
        "ok": True,
        "slug": slug,
        "csv": str(out_csv)
    }

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--language", default="en")
    parser.add_argument("--pass", dest="passthrough", nargs="*", default=[])
    # All other args should pass-through raw:
    parser.add_argument("extra", nargs="*", help="additional args passed directly to WhisperX")

    args = parser.parse_args()

    passthrough_args = []
    if args.passthrough:
        passthrough_args.extend(args.passthrough)
    if args.extra:
        passthrough_args.extend(args.extra)

    result = run_whisperx(
        args.slug,
        args.language,
        passthrough_args
    )

    print(json.dumps(result))

if __name__ == "__main__":
    main()
