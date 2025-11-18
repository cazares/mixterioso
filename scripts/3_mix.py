#!/usr/bin/env python3
# scripts/3_mix.py
#
# STEP 3: NON-BLOCKING DEMUCS (ALWAYS RUN, NEVER WAIT)
# -----------------------------------------------------
# - Launches Demucs asynchronously (background)
# - Immediately returns JSON so 0_master continues
# - Streams Demucs logs as they come
# - Never blocks Whisper/4_merge/5_gen pipeline
# - Absolutely cannot "hang" the master
#

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
import threading

RESET  = "\033[0m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
BLUE   = "\033[34m"

BASE     = Path(__file__).resolve().parent.parent
MP3_DIR  = BASE / "mp3s"
MIX_DIR  = BASE / "mixes"

MIX_DIR.mkdir(exist_ok=True)

# --------------------------------------------------------
# Logging
# --------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")

# --------------------------------------------------------
# Threaded log reader so main thread can exit immediately
# --------------------------------------------------------
def stream_output(proc):
    for line in proc.stdout:
        print(f"{CYAN}[Demucs]{RESET} {line.rstrip()}")
    proc.stdout.close()

# --------------------------------------------------------
# Launch demucs in background
# --------------------------------------------------------
def launch_demucs_background(slug):
    section = "Demucs"

    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        log(section, f"ERROR: Missing MP3 {mp3_path}", RED)
        return {"ok": False, "error": "mp3-not-found", "slug": slug}

    log(section, f"Launching Demucs (async) on {mp3_path}")

    cmd = [
        "demucs",
        "--two-stems", "vocals",   # also produces 6-stem files
        "--out", str(MIX_DIR),
        str(mp3_path)
    ]

    # Start NEW process group so it outlives this script
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True
    )

    # Spin up a log-reader thread (non-blocking)
    t = threading.Thread(target=stream_output, args=(proc,), daemon=True)
    t.start()

    # DO NOT WAIT. DO NOT BLOCK.
    log(section, f"Demucs running in background PID={proc.pid}", GREEN)

    return {
        "ok": True,
        "slug": slug,
        "pid": proc.pid,
        "running": True,
        "note": "Demucs running asynchronously; stems will appear in mixes/ when ready."
    }

# --------------------------------------------------------
# MAIN
# --------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--mode", default="vocals-100")  # retained for compatibility
    args = ap.parse_args()

    slug = args.slug

    result = launch_demucs_background(slug)

    # MASTER WAITS FOR THIS JSON TO ADVANCE — MUST PRINT IMMEDIATELY
    print(json.dumps(result))

if __name__ == "__main__":
    main()
