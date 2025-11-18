#!/usr/bin/env python3
# scripts/0_master.py
#
# FULL PIPELINE ORCHESTRATOR
# Compatible with Option A (2_download.py expects --task)
# Steps:
# 1_config.py
# 2_download.py  (lyrics, mp3, meta)
# 3_mix.py       (optional Demucs / stems)
# 4_merge.py     (WhisperX timings; always mp3-based)
# 5_gen.py       (mp4)
# 6_upload.py    (YouTube upload)
#
# New behavior:
# - As soon as lyrics + mp3 are ready (end of Step 2), launch 4_merge.py ASYNC.
# - WhisperX ALWAYS uses mp3s/<slug>.mp3 for alignment (not stems).
# - Demucs (3_mix.py) runs ONLY if needed (volumes != 100 or forced).
# - Full timing instrumentation + performance summary at the end.

import subprocess
import sys
import json
import shlex
import time
import threading
from pathlib import Path

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = Path(__file__).resolve().parent.parent
TIMINGS_DIR = BASE_DIR / "timings"

# ----------------------------------------------------------------------
# TIMING HELPERS
# ----------------------------------------------------------------------
TIMERS = {}


def t_start(label: str) -> None:
    """Mark the start time of a timed section."""
    TIMERS[label] = time.time()


def t_end(label: str, note: str | None = None) -> None:
    """Mark the end time of a timed section."""
    start = TIMERS.get(label)
    if isinstance(start, (int, float)):
        TIMERS[label] = time.time() - start
    else:
        # If there was no start, record zero but keep any note.
        TIMERS[label] = 0.0
    if note:
        # Store note alongside duration as a tuple: (seconds, note)
        val = TIMERS[label]
        TIMERS[label] = (val, note)


def t_mark(label: str, value: float | str, note: str | None = None) -> None:
    """Set an explicit value (e.g., 'skipped', 'cached')."""
    if note:
        TIMERS[label] = (value, note)
    else:
        TIMERS[label] = value


# ----------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


# ----------------------------------------------------------------------
# SAFE SUBPROCESS CALL (SYNC)
# Reads JSON result if available. Streams child output live.
# ----------------------------------------------------------------------
def run_step(cmd, section, timeout=9999):
    log(section, f"START  → {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
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
        except Exception:
            continue

    return json_obj, rc


# ----------------------------------------------------------------------
# ASYNC STEP LAUNCHER (no JSON capture, just logs)
# ----------------------------------------------------------------------
def launch_async_step(cmd, section):
    """
    Launch a step asynchronously, stream its logs in a background thread,
    and return the Popen process handle.
    """
    log(section, f"START (async) → {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _reader():
        for line in proc.stdout:
            print(f"{CYAN}[{section}]{RESET} {line.rstrip()}")
        proc.stdout.close()

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    return proc


# ----------------------------------------------------------------------
# FILE WAIT HELPER (for WhisperX CSV, etc.)
# ----------------------------------------------------------------------
def wait_for_file(path: Path, timeout: float = 900.0, poll: float = 1.0, label: str = "file"):
    """
    Wait until a file exists and is non-empty, or raise TimeoutError.
    """
    start = time.time()
    while time.time() - start < timeout:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(poll)
    raise TimeoutError(f"Timed out waiting for {label}: {path}")


# ----------------------------------------------------------------------
# PERFORMANCE SUMMARY
# ----------------------------------------------------------------------
def print_performance_summary():
    print("\n" + "=" * 55)
    print("               PERFORMANCE SUMMARY")
    print("=" * 55)

    def _extract(label):
        val = TIMERS.get(label, None)
        note = None
        if isinstance(val, tuple) and len(val) == 2:
            val, note = val
        return val, note

    label_map = [
        ("pipeline", "Total pipeline"),
        ("step1_config", "Step 1 (Config)"),
        ("step2_lyrics", "Step 2a (Lyrics)"),
        ("step2_meta", "Step 2b (Meta)"),
        ("step2_mp3", "Step 2c (MP3)"),
        ("whisperx", "WhisperX (timings)"),
        ("step3_mix", "Step 3 (Demucs Mix)"),
        ("step5_gen", "Step 5 (MP4 Gen)"),
        ("step6_upload", "Step 6 (Upload)"),
    ]

    durations = []

    for key, label in label_map:
        val, note = _extract(key)
        if isinstance(val, (int, float)):
            print(f"{label:25s}: {val:6.2f}s" + (f"  [{note}]" if note else ""))
            if key != "pipeline":
                durations.append((label, val))
        elif isinstance(val, str):
            print(f"{label:25s}: {val}")
        elif val is None:
            print(f"{label:25s}: (n/a)")
        else:
            print(f"{label:25s}: {val}")

    # Longest component (excluding total pipeline)
    if durations:
        longest_label, longest_val = max(durations, key=lambda x: x[1])
        print("-" * 55)
        print(f"Longest component: {longest_label} ({longest_val:.2f}s)")

    print("=" * 55 + "\n")


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
    # New CLI knobs for Demucs behavior:
    p.add_argument("--skip-demucs", action="store_true", help="Force skipping Demucs (mp3-only).")
    p.add_argument("--force-demucs", action="store_true", help="Force running Demucs regardless of config.")
    p.add_argument("--mp3-only", action="store_true", help="Alias for --skip-demucs.")
    args = p.parse_args()

    t_start("pipeline")

    log("Master", f"Pipeline starting for query: {args.query}")

    # ------------------------------------------------------------------
    # STEP 1 — CONFIG
    # ------------------------------------------------------------------
    log("Config", "Launching mixer & mode selector...")
    t_start("step1_config")

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
            "language": args.language or "en",
        }

    selected_lang = args.language or cfg_json["language"]
    selected_mode = args.mode or cfg_json["mode"]

    t_end("step1_config")
    log("Config", f"Selected config: {cfg_json}", GREEN)

    # Determine Demucs behavior
    all_100 = (
        cfg_json.get("vocals", 100) == 100 and
        cfg_json.get("bass", 100) == 100 and
        cfg_json.get("guitar", 100) == 100 and
        cfg_json.get("drums", 100) == 100
    )
    skip_flag = args.skip_demucs or args.mp3_only
    force_flag = args.force_demucs

    if force_flag:
        should_run_demucs = True
        note = "forced"
    elif skip_flag:
        should_run_demucs = False
        note = "skipped-via-cli"
    else:
        # Default rule: if user didn't change any levels and mode is vocals-100 → no Demucs.
        should_run_demucs = not (all_100 and selected_mode == "vocals-100")
        note = "auto" if should_run_demucs else "auto-skipped"

    log("Master", f"Demucs decision: should_run_demucs={should_run_demucs} ({note})", BLUE)

    # ------------------------------------------------------------------
    # STEP 2 — DOWNLOAD (three tasks)
    # ------------------------------------------------------------------
    # ---- A: LYRICS ----------------------------------------------------
    t_start("step2_lyrics")
    lyrics_json, rc = run_step(
        [
            "python3",
            "scripts/2_download.py",
            "--task", "lyrics",
            "--query", args.query,
            "--language", selected_lang,
        ],
        "Step2:Download"
    )
    t_end("step2_lyrics")

    if not lyrics_json or "slug" not in lyrics_json:
        log("Master", "ERROR: lyrics step failed to produce a slug", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    slug = lyrics_json["slug"]
    log("Master", f"Slug detected: {slug}", GREEN)

    # ---- B: META ------------------------------------------------------
    t_start("step2_meta")
    meta_json, rc = run_step(
        [
            "python3", "scripts/2_download.py",
            "--task", "meta",
            "--slug", slug,
            "--query", args.query,
        ],
        "Step2:Download"
    )
    t_end("step2_meta")

    # Fallback metadata
    meta_title = slug.replace("_", " ")
    meta_artist = ""

    if meta_json and meta_json.get("ok"):
        meta_title = meta_json.get("title", meta_title)
        meta_artist = meta_json.get("artist", meta_artist)
        log("Master", f"Metadata received: {meta_json}", GREEN)
    else:
        log("Master", "No metadata returned; continuing.", YELLOW)

    # ---- C: MP3 -------------------------------------------------------
    t_start("step2_mp3")
    mp3_json, rc = run_step(
        [
            "python3",
            "scripts/2_download.py",
            "--task", "mp3",
            "--slug", slug,
        ],
        "Step2:Download"
    )
    t_end("step2_mp3")

    if not mp3_json or not mp3_json.get("ok"):
        log("Master", "ERROR: mp3 step failed", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    log("Master", "MP3 downloaded.", GREEN)

    # ------------------------------------------------------------------
    # STEP 4 (LAUNCH EARLY) — MERGE (WhisperX) ASYNC
    #    - Uses mp3s/<slug>.mp3 for alignment.
    #    - Launched as soon as txt + mp3 exist (right after Step 2).
    # ------------------------------------------------------------------
    timings_csv_path = TIMINGS_DIR / f"{slug}.csv"
    whisper_proc = None

    t_start("whisperx")
    whisper_cmd = [
        "python3",
        "scripts/4_merge.py",
        "--slug", slug,
        "--language", selected_lang,
    ]
    whisper_proc = launch_async_step(whisper_cmd, "Step4:Merge")

    # ------------------------------------------------------------------
    # STEP 3 — MIX (Demucs), OPTIONAL
    # ------------------------------------------------------------------
    if should_run_demucs:
        t_start("step3_mix")
        mix_json, rc = run_step(
            [
                "python3", "scripts/3_mix.py",
                "--slug", slug,
                "--mode", selected_mode,
            ],
            "Step3:Mix"
        )
        t_end("step3_mix")
        if not mix_json or not mix_json.get("ok"):
            log("Master", "Mixing failed!", RED)
            t_end("pipeline")
            print_performance_summary()
            sys.exit(1)
    else:
        log("Master", "Skipping Demucs mix (mp3-only mode).", YELLOW)
        t_mark("step3_mix", "skipped")

    # ------------------------------------------------------------------
    # WAIT FOR WHISPERX TIMINGS CSV
    # ------------------------------------------------------------------
    try:
        log("Master", "Waiting for WhisperX timings CSV...", BLUE)
        wait_for_file(timings_csv_path, timeout=3600, poll=1.0, label="timings CSV")
        t_end("whisperx")
        log("Master", f"WhisperX timings ready: {timings_csv_path}", GREEN)
    except TimeoutError as e:
        log("Master", str(e), RED)
        if whisper_proc is not None:
            rc = whisper_proc.poll()
            log("Master", f"WhisperX process exit code: {rc}", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    # Optionally confirm WhisperX proc exit code
    if whisper_proc is not None:
        whisper_rc = whisper_proc.poll()
        if whisper_rc is None:
            # Still running; wait briefly
            whisper_proc.wait(timeout=5)
            whisper_rc = whisper_proc.returncode
        if whisper_rc != 0:
            log("Master", f"WhisperX process exited with code {whisper_rc}", YELLOW)

    # ------------------------------------------------------------------
    # STEP 5 — GEN (mp4)
    # ------------------------------------------------------------------
    t_start("step5_gen")
    gen_json, rc = run_step(
        [
            "python3", "scripts/5_gen.py",
            "--base-filename", slug,
            "--offset", str(args.offset),
            "--profile", selected_mode,
        ],
        "Step5:Gen"
    )
    t_end("step5_gen")

    if not gen_json or not gen_json.get("ok"):
        log("Master", "MP4 generation failed!", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    mp4_path = gen_json.get("mp4")
    log("Master", f"MP4 generated: {mp4_path}", GREEN)

    # ------------------------------------------------------------------
    # STEP 6 — UPLOAD (new CLI)
    # ------------------------------------------------------------------
    t_start("step6_upload")

    yt_title = f"{meta_title} - {meta_artist}" if meta_artist else meta_title
    yt_description = f"Karaoke generated automatically for '{meta_title}'"

    upload_json, rc = run_step(
        [
            "python3", "scripts/6_upload.py",
            "--mp4", mp4_path,
            "--title", yt_title,
            "--description", yt_description,
            "--base-filename", slug,
            "--visibility", "public",
        ],
        "Step6:Upload"
    )
    t_end("step6_upload")

    if not upload_json or not upload_json.get("ok"):
        log("Master", "Upload failed!", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    url = upload_json.get("watch_url", "<no-url>")
    log("Master", f"YouTube upload complete → {url}", GREEN)
    log("Master", "Pipeline complete", GREEN)

    t_end("pipeline")
    print_performance_summary()


if __name__ == "__main__":
    main()

# end of 0_master.py
