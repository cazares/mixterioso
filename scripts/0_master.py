#!/usr/bin/env python3
# scripts/0_master.py
#
# FULL PIPELINE ORCHESTRATOR
# Compatible with Option A (2_download.py expects --task)
# Steps:
# 1_config.py
# 2_download.py  (lyrics, mp3, meta)
# 3_mix.py
# 4_merge.py
# 5_gen.py
# 6_upload.py
#

import subprocess
import sys
import json
import shlex
import time
from pathlib import Path

RESET="\033[0m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
BLUE="\033[34m"

BASE_DIR = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# HIGH-LEVEL LOG WRAPPER
# ----------------------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


# ----------------------------------------------------------------------
# SAFE SUBPROCESS CALL
# Reads JSON result if available.
# Streams child output live.
# ----------------------------------------------------------------------
def run_step(cmd, section, timeout=9999):
    log(section, f"START  → {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    captured_lines = []
    start = time.time()

    for line in proc.stdout:
        captured_lines.append(line)
        print(f"{CYAN}[{section}]{RESET} {line.rstrip()}")

        if time.time() - start > timeout:
            proc.kill()
            log(section, f"TIMEOUT after {timeout}s", RED)
            return None, 124

    proc.wait()
    rc = proc.returncode

    if rc != 0:
        log(section, f"FAILED (code {rc})", RED)
        return None, rc

    # Try to extract final JSON from the bottom of output
    json_obj = None
    for line in reversed(captured_lines):
        try:
            json_obj = json.loads(line)
            break
        except:
            continue

    return json_obj, rc


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True)
    p.add_argument("--offset", type=float, default=-0.50)
    p.add_argument("--language", default=None)
    p.add_argument("--mode", default=None)
    args = p.parse_args()

    log("Master", f"Pipeline starting for query: {args.query}")

    # ------------------------------------------------------------------
    # STEP 1 — CONFIG
    # ------------------------------------------------------------------
    log("Config", "Launching mixer & mode selector...")

    cfg_json, rc = run_step(
        ["python3", "scripts/1_config.py"],
        "Config"
    )

    if cfg_json is None:
        log("Config", "Falling back to defaults", YELLOW)
        cfg_json = {
            "vocals": 100,
            "bass": 100,
            "guitar": 100,
            "drums": 100,
            "mode": args.mode or "vocals-100",
            "language": args.language or "en"
        }

    selected_lang = args.language or cfg_json["language"]
    selected_mode = args.mode or cfg_json["mode"]

    log("Config", f"Selected config: {cfg_json}", GREEN)

    # ------------------------------------------------------------------
    # STEP 2 — DOWNLOAD (A mode = three tasks)
    # ------------------------------------------------------------------
    # ---- A: LYRICS ----------------------------------------------------
    lyrics_json, rc = run_step(
        [
            "python3",
            "scripts/2_download.py",
            "--task", "lyrics",
            "--query", args.query,
            "--language", selected_lang
        ],
        "Step2:Download"
    )

    if not lyrics_json or "slug" not in lyrics_json:
        log("Master", "ERROR: lyrics step failed to produce a slug", RED)
        sys.exit(1)

    slug = lyrics_json["slug"]
    log("Master", f"Slug detected: {slug}", GREEN)

    # ---- B: META ------------------------------------------------------
    meta_json, rc = run_step(
        [
            "python3", "scripts/2_download.py",
            "--task", "meta",
            "--slug", slug,
            "--query", args.query
        ],
        "Step2:Download"
    )

    # Fallback metadata
    meta_title = slug.replace("_", " ")
    meta_artist = ""

    if meta_json and meta_json.get("ok"):
        meta_title = meta_json.get("title", meta_title)
        meta_artist = meta_json.get("artist", meta_artist)
        log("Master", f"Metadata received: {meta_json}", GREEN)
    else:
        log("Master", f"No metadata returned; continuing.", YELLOW)

    # ---- C: MP3 -------------------------------------------------------
    mp3_json, rc = run_step(
        [
            "python3",
            "scripts/2_download.py",
            "--task", "mp3",
            "--slug", slug
        ],
        "Step2:Download"
    )

    if not mp3_json:
        log("Master", "ERROR: mp3 step failed", RED)
        sys.exit(1)

    log("Master", "MP3 downloaded.", GREEN)

    # ------------------------------------------------------------------
    # STEP 3 — MIX (demucs)
    # ------------------------------------------------------------------
    mix_json, rc = run_step(
        [
            "python3", "scripts/3_mix.py",
            "--slug", slug,
            "--mode", selected_mode
        ],
        "Step3:Mix"
    )
    if not mix_json:
        log("Master", "Mixing failed!", RED)
        sys.exit(1)

    # ------------------------------------------------------------------
    # STEP 4 — MERGE (timings)
    # ------------------------------------------------------------------
    merge_json, rc = run_step(
        [
            "python3", "scripts/4_merge.py",
            "--slug", slug,
            "--language", selected_lang,
        ],
        "Step4:Merge"
    )
    if not merge_json:
        log("Master", "Auto-timing failed!", RED)
        sys.exit(1)

    # ------------------------------------------------------------------
    # STEP 5 — GEN (mp4)
    # ------------------------------------------------------------------
    gen_json, rc = run_step(
        [
            "python3", "scripts/5_gen.py",
            "--base-filename", slug,
            "--offset", str(args.offset),
            "--profile", selected_mode
        ],
        "Step5:Gen"
    )
    if not gen_json:
        log("Master", "MP4 generation failed!", RED)
        sys.exit(1)

    mp4_path = gen_json.get("mp4")
    log("Master", f"MP4 generated: {mp4_path}", GREEN)

    # ------------------------------------------------------------------
    # STEP 6 — UPLOAD (new CLI)
    # ------------------------------------------------------------------
    yt_title = f"{meta_title} - {meta_artist}" if meta_artist else meta_title
    yt_description = f"Karaoke generated automatically for '{meta_title}'"

    upload_json, rc = run_step(
        [
            "python3", "scripts/6_upload.py",
            "--mp4", mp4_path,
            "--title", yt_title,
            "--description", yt_description,
            "--base-filename", slug,
            "--visibility", "public"
        ],
        "Step6:Upload"
    )

    if not upload_json:
        log("Master", "Upload failed!", RED)
        sys.exit(1)

    url = upload_json.get("watch_url", "<no-url>")
    log("Master", f"YouTube upload complete → {url}", GREEN)
    log("Master", "Pipeline complete", GREEN)


if __name__ == "__main__":
    main()
