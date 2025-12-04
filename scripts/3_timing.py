#!/usr/bin/env python3
import sys
import csv
import time
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────
from mix_utils import (
    log, RESET, CYAN, GREEN, YELLOW, RED,
    slugify, PATHS, ask_yes_no, ffprobe_duration,
)

TXT_DIR     = PATHS["txt"]
MP3_DIR     = PATHS["mp3"]
TIMINGS_DIR = PATHS["timings"]


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def latest_slug_from_txt() -> str | None:
    """
    Return slug (filename without .txt) of the most recently modified txt,
    or None if no txt files exist.
    """
    txts = sorted(TXT_DIR.glob("*.txt"), key=lambda p: p.stat().st_mtime)
    if not txts:
        return None
    return txts[-1].stem


def pick_slug() -> str:
    """
    Ask user for slug, defaulting to latest txt slug.
    """
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

    latest = latest_slug_from_txt()
    if latest:
        prompt = f"Enter slug for timing (ENTER for latest='{latest}'): "
    else:
        prompt = "Enter slug for timing (no txts found yet, slug required): "

    try:
        s = input(prompt).strip()
    except EOFError:
        s = ""

    if not s:
        if not latest:
            raise SystemExit("No txt files found and no slug provided.")
        return latest

    return slugify(s)


def load_lyrics_lines(slug: str) -> list[tuple[int, str]]:
    """
    Load txts/<slug>.txt and return a list of (line_index, text),
    skipping empty/whitespace-only lines.
    """
    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        raise SystemExit(f"Missing lyrics txt: {txt_path}")

    raw = txt_path.read_text(encoding="utf-8").splitlines()
    lines: list[tuple[int, str]] = []
    idx = 0
    for line in raw:
        text = line.strip()
        if not text:
            continue
        lines.append((idx, text))
        idx += 1

    if not lines:
        raise SystemExit(f"Lyrics file has no non-empty lines: {txt_path}")

    return lines


def check_mp3(slug: str) -> Path:
    """
    Ensure mp3s/<slug>.mp3 exists, return its path.
    """
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if not mp3_path.exists():
        raise SystemExit(f"Missing mp3 audio: {mp3_path}")
    return mp3_path


def maybe_overwrite_timings(slug: str) -> Path:
    """
    Decide whether to overwrite existing timings file.
    """
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = TIMINGS_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return csv_path

    log("TIMING", f"Timings file already exists: {csv_path}", YELLOW)
    if not ask_yes_no("Overwrite existing timings file?", default_yes=False):
        raise SystemExit("Cancelled to avoid overwriting timings.")
    return csv_path


# ─────────────────────────────────────────────
# Manual timing loop
# ─────────────────────────────────────────────
def manual_timing(slug: str, lines: list[tuple[int, str]], mp3_path: Path, out_csv: Path) -> None:
    """
    Manual timing UI:
    - User starts playback of mp3 externally.
    - Press ENTER to start timer at the moment audio starts.
    - For each line, script shows text and waits for ENTER tap to record time.
    - Writes CSV with header: line_index,time_secs,text
    """
    print()
    log("TIMING", f"Slug: {slug}", CYAN)
    log("TIMING", f"Lyrics txt: {TXT_DIR / f'{slug}.txt'}", CYAN)
    log("TIMING", f"Audio mp3:  {mp3_path}", CYAN)

    dur = ffprobe_duration(mp3_path)
    if dur > 0:
        log("AUDIO", f"Detected duration ~{dur:.1f}s", GREEN)

    print()
    print("Manual timing instructions:")
    print("  1) Open the mp3 in your preferred player:")
    print(f"       {mp3_path}")
    print("  2) Seek to the beginning of the song.")
    print("  3) When you are READY TO START the song,")
    print("     press ENTER here at the exact moment you start playback.")
    print("  4) For each line shown, press ENTER when that line should appear.")
    print("  5) Type 'q' and press ENTER at any prompt to abort.")
    print()

    try:
        start_input = input("Press ENTER when you start the song (or 'q' to abort): ").strip().lower()
    except EOFError:
        start_input = "q"

    if start_input == "q":
        raise SystemExit("Manual timing aborted before start.")

    t0 = time.perf_counter()
    rows: list[tuple[int, float, str]] = []

    total = len(lines)
    print()
    log("TIMING", f"Starting timing for {total} lines...", CYAN)
    print()

    for idx, text in lines:
        print(f"[{idx + 1}/{total}] {text}")
        try:
            val = input("  Press ENTER at the moment for this line (or 'q' to abort): ").strip().lower()
        except EOFError:
            val = "q"

        if val == "q":
            raise SystemExit("Manual timing aborted by user.")

        now = time.perf_counter()
        t = now - t0
        rows.append((idx, t, text))
        log("TIMING", f"Line {idx} at {t:.3f}s", GREEN)
        print()

    # Write CSV
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "time_secs", "text"])
        for idx, t, text in rows:
            writer.writerow([idx, f"{t:.3f}", text])

    print()
    log("TIMING", f"Wrote timings CSV: {out_csv}", GREEN)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log("MODE", "Manual timing (no Whisper, no AI)", CYAN)

    slug = pick_slug()
    lines = load_lyrics_lines(slug)
    mp3_path = check_mp3(slug)
    out_csv = maybe_overwrite_timings(slug)

    manual_timing(slug, lines, mp3_path, out_csv)

    print()
    print(f"{GREEN}Done!{RESET} Timings written to: {out_csv}")


if __name__ == "__main__":
    main()

# end of 3_timing.py
