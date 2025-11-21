#!/usr/bin/env python3
# scripts/0_master.py
#
# FULL PIPELINE ORCHESTRATOR
#
# Steps:
#   Step 1 (built-in): Config / levels
#   Step 2: 1_download.py
#   Step 3: 2_mix.py
#   Step 4: 3_merge.py
#   Step 5: 4_gen.py
#   Step 6: 5_upload.py
#
# Step 1 is native to this file — no subprocess.
# All stems default to 100 unless flags override them.
#
# Examples:
#   python3 scripts/0_master.py --query "Come As You Are"
#   python3 scripts/0_master.py --query "Californication" --bass 0
#
# Behavior:
# - As soon as lyrics + mp3 are ready (end of Step 2), launch 3_merge.py ASYNC.
# - WhisperX ALWAYS uses mp3s/<slug>.mp3 for alignment (not stems).
# - Demucs runs only if any stem != 100.
# - Master waits for timings CSV before Step 5.
# - Always prints performance summary at the end.

import argparse
import json
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
# Timing helpers
# -------------------------------------------------
TIMERS: dict[str, float | tuple[float, str]] = {}


def t_start(label: str) -> None:
    TIMERS[label] = time.time()


def t_end(label: str, note: str | None = None) -> None:
    if label in TIMERS and isinstance(TIMERS[label], (int, float)):
        elapsed = time.time() - float(TIMERS[label])
    else:
        elapsed = 0.0
    TIMERS[label] = elapsed if note is None else (elapsed, note)


def t_mark(label: str, value: float | str, note: str | None = None) -> None:
    TIMERS[label] = (value, note) if note else value


# -------------------------------------------------
# Logging helpers
# -------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)


def log_error(section: str, msg: str) -> None:
    log(section, msg, RED)


# -------------------------------------------------
# run_step — JSON capture
# -------------------------------------------------
def run_step(cmd, section: str):
    cmd = [str(x) for x in cmd]
    log(section, f"START  → {' '.join(cmd)}", BLUE)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    result_json = None
    json_lines: list[str] = []
    capturing = False
    brace_depth = 0

    for raw in proc.stdout:
        line = raw.rstrip("\n")
        print(f"[{section}] {line}")
        stripped = line.strip()

        if capturing:
            json_lines.append(stripped)
            brace_depth += stripped.count("{") - stripped.count("}")
            if brace_depth <= 0:
                capturing = False
                try:
                    result_json = json.loads("\n".join(json_lines))
                except Exception:
                    result_json = None
            continue

        if "{" in stripped:
            idx = stripped.find("{")
            js = stripped[idx:]
            if js.startswith("{"):
                json_lines = [js]
                capturing = True
                brace_depth = js.count("{") - js.count("}")
                if brace_depth <= 0:
                    capturing = False
                    try:
                        result_json = json.loads(js)
                    except Exception:
                        result_json = None
                continue

    proc.wait()

    if result_json is None:
        print(f"[{section}] WARNING: No JSON captured (rc={proc.returncode})")

    return result_json, proc.returncode


# -------------------------------------------------
# Async step runner
# -------------------------------------------------
def launch_async_step(cmd, section: str):
    log(section, f"START (async) → {' '.join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def reader():
        for line in proc.stdout:
            print(f"{CYAN}[{section}]{RESET} {line.rstrip()}")
        proc.stdout.close()
        rc = proc.wait()
        print(f"{RED}[{section}] Process exited with code {rc}{RESET}", flush=True)

    threading.Thread(target=reader, daemon=True).start()
    return proc


# -------------------------------------------------
# wait for file
# -------------------------------------------------
def wait_for_file(path: Path, timeout: float = 900, poll: float = 1.0, label: str = "file"):
    start = time.time()
    while True:
        if path.exists():
            return
        if time.time() - start > timeout:
            raise TimeoutError(f"Timed out waiting for {label}: {path}")
        time.sleep(poll)


# -------------------------------------------------
# CLI parser
# -------------------------------------------------
def build_arg_parser():
    p = argparse.ArgumentParser(description="Karaoke pipeline")

    # Query / slug
    p.add_argument("--slug")
    p.add_argument("--query")

    # Stems — default=100
    p.add_argument("--vocals", type=int)
    p.add_argument("--bass", type=int)
    p.add_argument("--guitar", type=int)
    p.add_argument("--drums", type=int)

    # Language + mode
    p.add_argument("--language")
    p.add_argument("--mode")

    # Offset + skip upload
    p.add_argument("--offset", type=float)
    p.add_argument("--no-upload", action="store_true")

    # Pass-through to underlying scripts
    p.add_argument(
        "--pass",
        dest="passthrough",
        nargs="*",
        default=[],
        help="Additional args to pass through to underlying scripts.",
    )
    return p


# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    t_start("pipeline")

    parser = build_arg_parser()
    args = parser.parse_args()

    slug = args.slug
    query = args.query

    # --------------------------------------------
    # Prompt for query if none provided
    # --------------------------------------------
    if not slug and not query:
        print(f"{BOLD}{CYAN}Enter slug or query:{RESET}")
        entered = input("> ").strip()
        if not entered:
            log("Master", "No slug/query provided.", YELLOW)
            t_end("pipeline")
            sys.exit(1)
        query = entered

    log("Master", f"Starting pipeline for {query or slug}", CYAN)

    # -------------------------------------------------
    # STEP 1 — built-in config
    # -------------------------------------------------
    t_start("step1_config")

    levels = {
        "vocals": 100 if args.vocals is None else args.vocals,
        "bass":   100 if args.bass   is None else args.bass,
        "guitar": 100 if args.guitar is None else args.guitar,
        "drums":  100 if args.drums  is None else args.drums,
    }

    selected_lang = args.language or "en"
    selected_mode = args.mode or "custom"

    log("Config", f"Stem levels: {levels}", GREEN)
    log("Config", f"Mode={selected_mode}, Language={selected_lang}", GREEN)

    all_100 = (
        levels["vocals"] == 100 and
        levels["bass"]   == 100 and
        levels["guitar"] == 100 and
        levels["drums"]  == 100
    )

    should_run_demucs = not all_100
    log(
        "Master",
        f"Demucs decision: should_run_demucs={should_run_demucs}",
        GREEN if should_run_demucs else YELLOW,
    )

    # -------------------------------------------------
    # CONFIRMATION PROMPT (default = Yes)
    # -------------------------------------------------
    try:
        resp = input("Proceed with these settings? [Y/n]: ").strip().lower()
    except EOFError:
        resp = ""  # non-interactive: default to yes

    if resp and not resp.startswith("y"):
        print(f"\n{YELLOW}{BOLD}Aborted by user. Showing help (-h):{RESET}\n")
        print(parser.format_help())
        t_end("pipeline")
        sys.exit(0)

    t_end("step1_config")

    # -------------------------------------------------
    # STEP 2 — 1_download.py (lyrics, meta, mp3)
    # -------------------------------------------------
    if not slug and query:
        t_start("step2_lyrics")
        lyrics_json, rc = run_step(
            [
                "python3",
                "scripts/1_download.py",
                "--task", "lyrics",
                "--query", query,
                "--language", selected_lang,
            ],
            "Step2:Download",
        )
        t_end("step2_lyrics")

        if not lyrics_json or not lyrics_json.get("ok"):
            log_error("Master", f"Lyrics step failed: json={lyrics_json}, rc={rc}")
            t_end("pipeline")
            sys.exit(1)

        slug = lyrics_json.get("slug")

    if not slug:
        log_error("Master", "Slug could not be determined from lyrics step.")
        t_end("pipeline")
        sys.exit(1)

    log("Master", f"Slug detected: {slug}", GREEN)

    # META
    t_start("step2_meta")
    meta_cmd = [
        "python3",
        "scripts/1_download.py",
        "--task", "meta",
        "--slug", slug,
    ]
    if query:
        meta_cmd.extend(["--query", query])

    meta_json, rc = run_step(meta_cmd, "Step2:Download")
    t_end("step2_meta")

    if not meta_json or not meta_json.get("ok"):
        log_error("Master", f"Metadata step failed: json={meta_json}, rc={rc}")
        t_end("pipeline")
        sys.exit(1)

    artist = meta_json.get("artist", "Unknown Artist")
    title = meta_json.get("title", slug)
    log("Master", f"Metadata received: {meta_json}", GREEN)

    # MP3
    t_start("step2_mp3")
    mp3_json, rc = run_step(
        [
            "python3",
            "scripts/1_download.py",
            "--task", "mp3",
            "--slug", slug,
        ],
        "Step2:Download",
    )
    t_end("step2_mp3")

    if not mp3_json or not mp3_json.get("ok"):
        log_error("Master", f"MP3 download step failed: json={mp3_json}, rc={rc}")
        t_end("pipeline")
        sys.exit(1)

    mp3_path = mp3_json.get("mp3_path")
    if not mp3_path:
        log_error("Master", "MP3 path missing from Step 2 JSON.")
        t_end("pipeline")
        sys.exit(1)

    log("Master", "MP3 downloaded.", GREEN)

    # -------------------------------------------------
    # STEP 4 (LAUNCH EARLY) — 3_merge.py (WhisperX) ASYNC
    # -------------------------------------------------
    timings_csv_path = TIMINGS_DIR / f"{slug}.csv"
    whisper_proc = None

    t_start("whisperx")
    whisper_cmd = [
        "python3",
        "scripts/3_merge.py",
        "--slug", slug,
        "--language", selected_lang,
    ]
    whisper_proc = launch_async_step(whisper_cmd, "Step4:Merge")

    # -------------------------------------------------
    # STEP 3 — 2_mix.py (Demucs), OPTIONAL
    # -------------------------------------------------
    if should_run_demucs:
        t_start("step3_mix")
        mix_json, rc = run_step(
            [
                "python3", "scripts/2_mix.py",
                "--slug", slug,
                "--mode", selected_mode,
            ],
            "Step3:Mix",
        )
        t_end("step3_mix")
        if not mix_json or not mix_json.get("ok"):
            log("Master", f"Mixing failed! json={mix_json}, rc={rc}", RED)
            t_end("pipeline")
            sys.exit(1)
    else:
        log("Master", "Skipping Demucs mix (mp3-only mode; all levels 100).", YELLOW)
        t_mark("step3_mix", "skipped")

    # -------------------------------------------------
    # WAIT FOR WHISPERX TIMINGS CSV
    # -------------------------------------------------
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
        sys.exit(1)

    if whisper_proc is not None:
        whisper_rc = whisper_proc.wait()
        if whisper_rc != 0:
            log("Master", f"WhisperX process exited with code {whisper_rc}", YELLOW)

    # -------------------------------------------------
    # STEP 5 — 4_gen.py (MP4)
    # -------------------------------------------------
    t_start("step5_gen")

    offset = args.offset if args.offset is not None else 0.0
    base_filename = slug
    gen_cmd = [
        "python3",
        "scripts/4_gen.py",
        "--slug", base_filename,
        "--offset", str(offset),
    ]
    if args.passthrough:
        gen_cmd.extend(["--pass", *args.passthrough])

    gen_json, rc = run_step(gen_cmd, "Step5:Gen")
    t_end("step5_gen")

    if not gen_json or not gen_json.get("ok"):
        log("Master", f"MP4 generation failed! json={gen_json}, rc={rc}", RED)
        t_end("pipeline")
        sys.exit(1)

    mp4_path = (
        gen_json.get("file")
        or gen_json.get("mp4")
        or gen_json.get("mp4_path")
    )
    log("Master", f"MP4 generated at: {mp4_path}", GREEN)

    # -------------------------------------------------
    # STEP 6 — 5_upload.py (optional)
    # -------------------------------------------------
    if args.no_upload:
        log("Master", "Upload skipped (--no-upload).", YELLOW)
        t_mark("step6_upload", "skipped")
        t_end("pipeline")
        return

    t_start("step6_upload")
    upload_cmd = [
        "python3",
        "scripts/5_upload.py",
        "--file", mp4_path,
        "--slug", base_filename,
        "--title", title,
    ]
    if args.passthrough:
        upload_cmd.extend(["--pass", *args.passthrough])

    upload_json, rc = run_step(upload_cmd, "Step6:Upload")
    t_end("step6_upload")

    if not upload_json or not upload_json.get("ok"):
        log("Master", f"Upload step failed. json={upload_json}, rc={rc}", RED)
        t_end("pipeline")
        sys.exit(1)

    url = upload_json.get("watch_url", "<no-url>")
    log("Master", f"YouTube upload complete → {url}", GREEN)
    log("Master", "Pipeline complete", GREEN)

    t_end("pipeline")


if __name__ == "__main__":
    main()

# end of 0_master.py
