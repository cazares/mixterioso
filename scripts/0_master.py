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
# - Demucs can run in parallel with WhisperX (if needed).
# - Master waits for timings CSV before Step 5.
# - Always prints performance summary at the end.

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE_DIR = REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
MIXES_DIR = BASE_DIR / "mixes"
OUTPUT_DIR = BASE_DIR / "output"
META_DIR = BASE_DIR / "meta"

TIMINGS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
META_DIR.mkdir(exist_ok=True)

# -------------------------------------------------
# Simple timing/performance bookkeeping
# -------------------------------------------------
TIMERS: dict[str, float | tuple[float, str]] = {}


def t_start(label: str) -> None:
    TIMERS[label] = time.time()


def t_end(label: str, note: str | None = None) -> None:
    if label in TIMERS and isinstance(TIMERS[label], (int, float)):
        start = float(TIMERS[label])
        elapsed = time.time() - start
        TIMERS[label] = elapsed if note is None else (elapsed, note)
    else:
        # no start recorded, treat as 0
        elapsed = 0.0
        TIMERS[label] = elapsed if note is None else (elapsed, note)

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


# -------------------------------------------------
# Logging helpers
# -------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)


def log_error(section: str, msg: str) -> None:
    log(section, msg, RED)


def _format_duration(sec: float | int) -> str:
    """Pretty-print duration as seconds, plus mm:ss when >= 60."""
    if not isinstance(sec, (int, float)):
        return str(sec)
    if sec < 0:
        sec = 0.0
    if sec < 60:
        return f"{sec:6.2f}s"
    minutes = int(sec // 60)
    rem = sec - minutes * 60
    return f"{sec:6.2f}s (~{minutes}m {rem:04.1f}s)"


def print_performance_summary() -> None:
    print("\n" + "=" * 55)
    print("               PERFORMANCE SUMMARY")
    print("=" * 55)

    def get(label, default=None):
        v = TIMERS.get(label, default)
        if isinstance(v, tuple):
            return v[0]
        return v

    pipeline_total = get("pipeline")
    if isinstance(pipeline_total, (int, float)):
        print(f"Total pipeline           : {_format_duration(pipeline_total)}")

    labels = [
        ("step1_config", "Step 1 (Config)"),
        ("step2_lyrics", "Step 2a (Lyrics)"),
        ("step2_meta",   "Step 2b (Meta)"),
        ("step2_mp3",    "Step 2c (MP3)"),
        ("whisperx",     "WhisperX (timings)"),
        ("step3_mix",    "Step 3 (Demucs Mix)"),
        ("step5_gen",    "Step 5 (MP4 Gen)"),
        ("step6_upload", "Step 6 (Upload)"),
    ]

    longest_label = None
    longest_time: float = -1.0

    for key, desc in labels:
        v = TIMERS.get(key)
        if v is None:
            print(f"{desc:24}: (n/a)")
            continue
        note = None
        if isinstance(v, tuple):
            # (value, note) or ((value, note), extra_note)
            if isinstance(v[0], tuple):
                val, note = v[0]
            else:
                val, note = v
        else:
            val = v

        if isinstance(val, (int, float)):
            print(f"{desc:24}: {_format_duration(val)}", end="")
            if note:
                print(f"  [{note}]")
            else:
                print()
            if desc.startswith("Step") or desc.startswith("WhisperX"):
                if val > longest_time:
                    longest_time = val
                    longest_label = desc
        else:
            print(f"{desc:24}: {val}")
    if longest_label is not None:
        print(f"Longest component: {longest_label} ({longest_time:0.2f}s)")
    print("=" * 55 + "\n")


# ----------------------------------------------------------------------
# RUN STEP HELPER (blocking, JSON on last line) — DEBUG MODE
# ----------------------------------------------------------------------
def run_step(cmd, section):
    cmd = [str(x) if x is not None else "" for x in cmd]

    log(section, f"START  → {' '.join(cmd)}", BLUE)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    result_json = None
    json_lines = []
    capturing = False

    for raw in proc.stdout:
        line = raw.rstrip("\n")
        print(f"[{section}] {line}")

        stripped = line

        # JSON candidate
        idx = stripped.find("{")
        possible = stripped[idx:] if idx != -1 else ""

        # ---------------------------------------------------------
        # JSON START
        # ---------------------------------------------------------
        if not capturing and possible.startswith("{") and ":" in possible:
            json_lines = [possible]
            capturing = True

            # If JSON starts and ends on SAME line → parse immediately
            if possible.rstrip().endswith("}"):
                try:
                    result_json = json.loads(possible)
                except Exception as e:
                    print(f"[{section}] JSON parse error: {e}")
                capturing = False
            continue

        # ---------------------------------------------------------
        # JSON CONTINUATION
        # ---------------------------------------------------------
        if capturing:
            json_lines.append(possible)

            # JSON END?
            if possible.rstrip().endswith("}"):
                capturing = False
                merged = "\n".join(json_lines)
                try:
                    result_json = json.loads(merged)
                except Exception as e:
                    print(f"[{section}] JSON parse error: {e}")
            continue

    proc.wait()
    return result_json, proc.returncode

# ----------------------------------------------------------------------
# ASYNC LAUNCH (for WhisperX step)
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
    Wait for a file to appear, polling at `poll` seconds, up to `timeout` seconds.
    Raises TimeoutError on failure.
    """
    start = time.time()
    while True:
        if path.exists():
            return
        if time.time() - start > timeout:
            raise TimeoutError(f"Timed out waiting for {label}: {path}")
        time.sleep(poll)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def build_arg_parser():
    p = argparse.ArgumentParser(description="Karaoke Time pipeline master.")
    p.add_argument("--slug", help="Slug (if known).")
    p.add_argument("--query", help="Search query (if slug not provided).")
    p.add_argument("--offset", type=float, default=None, help="Global offset for MP4.")
    p.add_argument("--mode", help="Mix mode (e.g., vocals-100).")
    p.add_argument("--language", help="Lyrics/ASR language (e.g., en, es).")
    p.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip YouTube upload step.",
    )
    p.add_argument(
        "--pass",
        dest="passthrough",
        nargs="*",
        default=[],
        help="Additional args to pass through to underlying scripts.",
    )
    return p


def main():
    t_start("pipeline")

    parser = build_arg_parser()
    args = parser.parse_args()

    slug = args.slug
    query = args.query

    if not slug and not query:
        # Interactive slug/query prompt
        print(f"{BOLD}{CYAN}Enter slug or query for the song:{RESET}")
        print("  - Leave blank to cancel")
        entered = input("> ").strip()
        if not entered:
            log("Master", "No slug or query provided. Exiting.", YELLOW)
            t_end("pipeline")
            print_performance_summary()
            sys.exit(1)
        # For now, treat as query; slug will be inferred later.
        query = entered

    log("Master", f"Pipeline starting for query: {query or slug}", CYAN)

    # ------------------------------------------------------------------
    # STEP 1 — CONFIG (Mixer/Mode Selector)
    # ------------------------------------------------------------------
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

    should_run_demucs = not all_100
    log(
        "Master",
        f"Demucs decision: should_run_demucs={should_run_demucs} (auto-skipped)"
        if not should_run_demucs
        else f"Demucs decision: should_run_demucs={should_run_demucs}",
        YELLOW if not should_run_demucs else GREEN,
    )

    slug = args.slug
    # ------------------------------------------------------------------
    # STEP 2 — DOWNLOAD (lyrics, meta, mp3)
    # ------------------------------------------------------------------
    if not slug and query:
        # First call: lyrics by query (also infers slug)
        t_start("step2_lyrics")
        lyrics_cmd = [
            "python3",
            "scripts/2_download.py",
            "--task", "lyrics",
            "--query", query,
            "--language", selected_lang,
        ]
        lyrics_json, rc = run_step(lyrics_cmd, "Step2:Download")
        t_end("step2_lyrics")

        # if not lyrics_json or not lyrics_json.get("ok"):
        #     log_error("Master", "Lyrics step failed to produce a slug")
        #     t_end("pipeline")
        #     print_performance_summary()
        #     sys.exit(1)

        slug = lyrics_json.get("slug")

    if not slug:
        log_error("Master", "Slug could not be determined from lyrics step.")
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    log("Master", f"Slug detected: {slug}", GREEN)

    # STEP 2b: META
    t_start("step2_meta")
    meta_cmd = [
        "python3",
        "scripts/2_download.py",
        "--task", "meta",
        "--slug", slug,
    ]
    if query:
        meta_cmd.extend(["--query", query])
    meta_json, rc = run_step(meta_cmd, "Step2:Download")
    t_end("step2_meta")

    if not meta_json or not meta_json.get("ok"):
        log_error("Master", "Metadata step failed.")
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    artist = meta_json.get("artist", "Unknown Artist")
    title = meta_json.get("title", slug)
    log("Master", f"Metadata received: {meta_json}", GREEN)

    # STEP 2c: MP3
    t_start("step2_mp3")
    mp3_cmd = [
        "python3",
        "scripts/2_download.py",
        "--task", "mp3",
        "--slug", slug,
    ]
    mp3_json, rc = run_step(mp3_cmd, "Step2:Download")
    t_end("step2_mp3")

    if not mp3_json or not mp3_json.get("ok"):
        log_error("Master", "MP3 download step failed.")
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    mp3_path = mp3_json.get("mp3_path")
    if not mp3_path:
        log_error("Master", "MP3 path missing from Step 2 JSON.")
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
    # ----------------------------------------------
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
        # At this point the timings CSV already exists, so 4_merge.py
        # should be finishing up. Wait for it to exit without a hard timeout.
        whisper_rc = whisper_proc.wait()
        if whisper_rc != 0:
            log("Master", f"WhisperX process exited with code {whisper_rc}", YELLOW)

    # ------------------------------------------------------------------
    # STEP 5 — GEN (mp4)
    # ------------------------------------------------------------------
    t_start("step5_gen")

    offset = args.offset if args.offset is not None else 0.0
    base_filename = slug
    gen_cmd = [
        "python3",
        "scripts/5_gen.py",
        "--slug", base_filename,
        "--offset", str(offset),
        # "--profile", selected_mode,
        # "--artist", artist,
        # "--title", title,
    ]
    if args.passthrough:
        gen_cmd.extend(["--pass", *args.passthrough])

    gen_json, rc = run_step(gen_cmd, "Step5:Gen")
    t_end("step5_gen")

    if not gen_json or not gen_json.get("ok"):
        log("Master", "MP4 generation failed!", RED)
        t_end("pipeline")
        print_performance_summary()
        sys.exit(1)

    mp4_path = (
        gen_json.get("file")
        or gen_json.get("mp4")
        or gen_json.get("mp4_path")
    )
    log("Master", f"MP4 generated at: {mp4_path}", GREEN)

    # ------------------------------------------------------------------
    # STEP 6 — UPLOAD (optional)
    # ------------------------------------------------------------------
    if args.no_upload:
        log("Master", "Upload skipped (--no-upload).", YELLOW)
        t_mark("step6_upload", "skipped")
        t_end("pipeline")
        print_performance_summary()
        return

    t_start("step6_upload")
    upload_cmd = [
        "python3",
        "scripts/6_upload.py",
        "--file", mp4_path,
        "--slug", base_filename,
        "--title", title,
    ]
    if args.passthrough:
        upload_cmd.extend(["--pass", *args.passthrough])

    upload_json, rc = run_step(upload_cmd, "Step6:Upload")
    t_end("step6_upload")

    if not upload_json or not upload_json.get("ok"):
        log("Master", "Upload step failed.", RED)
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
