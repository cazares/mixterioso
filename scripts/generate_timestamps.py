#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps.py
Generates karaoke_time_by_miguel.py-compatible CSV timing data from audio.
Now uses WhisperX forced alignment (float32 mode) and skips fade effects.
"""

import csv, logging, subprocess, sys
from pathlib import Path

# ------------------------- Directories ------------------------- #
ROOT_DIR = Path(__file__).resolve().parent.parent
SONGS_DIR = ROOT_DIR / "songs"
LYRICS_DIR = ROOT_DIR / "lyrics"
SONGS_DIR.mkdir(exist_ok=True)
LYRICS_DIR.mkdir(exist_ok=True)

# ------------------------- Color Codes ------------------------- #
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
RESET = "\033[0m"

# ------------------------- Logging ----------------------------- #
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

# ------------------------- Helpers ----------------------------- #
def sanitize_filename(name: str) -> str:
    """Allow only alphanumeric + underscores."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name).strip("_")

def download_audio(url: str, title_hint: str = None) -> Path:
    """Download YouTube audio via yt-dlp and return MP3 path."""
    logging.info(f"{BLUE}🎥 Downloading from YouTube:{RESET} {url}")
    output_path = SONGS_DIR / "%(title)s.%(ext)s"
    cmd = [
        "yt-dlp", "-x", "--audio-format", "mp3", "--no-playlist",
        "-o", str(output_path),
        url,
    ]
    subprocess.run(cmd, check=True, text=True)
    mp3_files = sorted(SONGS_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = mp3_files[0]
    fixed_name = sanitize_filename(title_hint or latest.stem) + ".mp3"
    fixed_path = latest.with_name(fixed_name)
    if latest != fixed_path:
        latest.rename(fixed_path)
    logging.info(f"{GREEN}✅ Download complete →{RESET} {fixed_path.name}")
    return fixed_path

# ------------------------- WhisperX ----------------------------- #
def whisperx_segments(audio_path: Path, txt_path: Path):
    """Run WhisperX forced alignment (float32 mode, CPU)."""
    try:
        import whisperx
    except ImportError:
        logging.info(f"{YELLOW}⚠️ WhisperX not installed; skipping.{RESET}")
        return []

    try:
        logging.info(f"{MAGENTA}🎧 Running WhisperX forced alignment:{RESET} {audio_path.name}")
        model = whisperx.load_model("small", device="cpu", compute_type="float32")
        result = model.transcribe(str(audio_path))
        if not result.get("segments"):
            logging.error(f"{RED}WhisperX transcription failed.{RESET}")
            return []

        align_model, metadata = whisperx.load_align_model(language_code="en", device="cpu")
        aligned = whisperx.align(result["segments"], align_model, metadata, str(audio_path), device="cpu")
        segs = aligned.get("segments", [])
        if not segs:
            logging.error(f"{RED}No aligned segments returned.{RESET}")
            return []

        # Replace text with lyric lines (if available)
        if txt_path.exists():
            lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            for i, s in enumerate(segs):
                s["text"] = lines[i] if i < len(lines) else s.get("text", "")
        logging.info(f"{GREEN}✅ WhisperX aligned {len(segs)} segments (float32 mode).{RESET}")
        return segs
    except Exception as e:
        logging.error(f"{RED}WhisperX alignment failed:{RESET} {e}")
        return []

# ------------------------- CSV Handling ------------------------- #
def write_karaoke_csv(csv_path: Path, txt_path: Path, segs: list):
    """Write karaoke_time_by_miguel.py-compatible CSV with start times only."""
    if not txt_path.exists():
        logging.error(f"{RED}Lyrics file not found:{RESET} {txt_path}")
        return

    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        for i, seg in enumerate(segs):
            start_time = float(seg.get("start", 0.0))
            lyric_line = lines[i] if i < len(lines) else seg.get("text", f"line_{i+1}")
            writer.writerow([lyric_line, f"{start_time:.3f}"])
    logging.info(f"{GREEN}✅ Wrote CSV (start times only):{RESET} {csv_path}")

def validate_and_fix_csv(csv_path: Path):
    """Ensure CSV has headers ['line','start']."""
    try:
        with csv_path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if len(rows) <= 1:
            logging.warning(f"{csv_path.name} is empty or header-only.")
            return False
        headers = [h.strip().lower() for h in rows[0]]
        if headers == ["line", "start"]:
            logging.info(f"✅ CSV already valid: {csv_path.name}")
            return True
        if set(headers) >= {"start", "end", "text"}:
            fixed = [["line", "start"]] + [[r[2], r[0]] for r in rows[1:] if len(r) >= 3]
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fixed)
            logging.info(f"🩹 Fixed [start,end,text] CSV → {csv_path.name}")
            return True
        logging.warning(f"❓ Unknown CSV format in {csv_path.name}")
        return False
    except Exception as e:
        logging.error(f"Error validating CSV: {e}")
        return False

# ------------------------- Line Count Check ---------------------- #
def warn_if_linecount_mismatch(txt_path: Path, csv_path: Path):
    """Compare line counts between lyrics TXT and timestamps CSV, and warn if mismatch."""
    try:
        if not txt_path.exists() or not csv_path.exists():
            return

        # Read text lines
        text_lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        # Read CSV lines (skip header)
        with csv_path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        csv_lines = [r[0].strip() for r in rows[1:] if len(r) >= 1 and r[0].strip()]

        txt_count, csv_count = len(text_lines), len(csv_lines)

        if txt_count != csv_count:
            diff = abs(txt_count - csv_count)
            logging.warning(
                f"\n{YELLOW}⚠️  MISMATCH DETECTED between lyrics and timestamps:{RESET}\n"
                f"  Lyrics TXT lines : {txt_count}\n"
                f"  CSV timestamp lines : {csv_count}\n"
                f"  Difference : {diff} line(s)\n"
            )
            logging.warning(
                f"{MAGENTA}🔍 Preview of differences:{RESET}\n"
                f"  First few TXT lines ({min(5, txt_count)}): {text_lines[:5]}\n"
                f"  First few CSV lines ({min(5, csv_count)}): {csv_lines[:5]}\n"
            )
            logging.warning(
                f"{YELLOW}💡 Fix suggestion:{RESET} Re-run forced alignment or manually trim/extend CSV to match lyrics."
            )
        else:
            logging.info(f"{GREEN}✅ Line counts match ({txt_count} each).{RESET}")

    except Exception as e:
        logging.error(f"{RED}Error comparing line counts:{RESET} {e}")

# ------------------------- Main CLI ----------------------------- #
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate timestamp CSVs for karaoke_time_by_miguel.py")
    ap.add_argument("--audio", help="Path to .mp3 or YouTube URL", required=True)
    ap.add_argument("--text", help="Path to .txt lyrics file", required=True)
    ap.add_argument("--title", help="Optional clean title hint for naming", default=None)
    args = ap.parse_args()

    txt_path = Path(args.text)
    csv_name = sanitize_filename(txt_path.stem) + "_timestamps.csv"
    csv_path = LYRICS_DIR / csv_name

    # 🔍 Check if MP3 already exists
    mp3_path = SONGS_DIR / (sanitize_filename(args.title or txt_path.stem) + ".mp3")
    if mp3_path.exists():
        logging.info(f"🎵 Using existing MP3 → {mp3_path.name}")
        audio_path = mp3_path
    else:
        if str(args.audio).startswith(("http://", "https://")):
            audio_path = download_audio(args.audio, title_hint=args.title or txt_path.stem)
        else:
            audio_path = Path(args.audio).expanduser().resolve()

    # Validate or regenerate CSV
    if csv_path.exists():
        logging.info(f"📂 Found existing CSV → {csv_path.name}")
        if validate_and_fix_csv(csv_path):
            logging.info("✅ CSV valid. Skipping transcription.")
            warn_if_linecount_mismatch(txt_path, csv_path)
            return
        logging.info("🔁 CSV invalid or empty, regenerating...")

    segs = whisperx_segments(audio_path, txt_path)
    if not segs:
        logging.error("💀 No segments returned. Aborting.")
        return

    write_karaoke_csv(csv_path, txt_path, segs)

    # Warn about mismatched line counts
    warn_if_linecount_mismatch(txt_path, csv_path)

    logging.info(f"{GREEN}✅ Done. CSV saved at:{RESET} {csv_path}")

# ------------------------- Entry Point -------------------------- #
if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Error: {e}")
        sys.exit(1)

# end of generate_timestamps.py
