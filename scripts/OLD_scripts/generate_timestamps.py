#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_timestamps.py
Generates karaoke_time_by_miguel.py-compatible CSV timing data from audio.
Now uses WhisperX or Faster-Whisper (--refresh-csv) and decodes audio via ffmpeg-python.
"""

import csv, logging, subprocess, sys, json, tempfile
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
            return False
        headers = [h.strip().lower() for h in rows[0]]
        if headers == ["line", "start"]:
            return True
        if set(headers) >= {"start", "end", "text"}:
            fixed = [["line", "start"]] + [[r[2], r[0]] for r in rows[1:] if len(r) >= 3]
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(fixed)
            return True
        return False
    except Exception:
        return False

def warn_if_linecount_mismatch(txt_path: Path, csv_path: Path):
    try:
        if not txt_path.exists() or not csv_path.exists():
            return
        text_lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        with csv_path.open("r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        csv_lines = [r[0].strip() for r in rows[1:] if len(r) >= 1 and r[0].strip()]
        if len(text_lines) != len(csv_lines):
            logging.warning(f"⚠️ Lyrics {len(text_lines)} vs CSV {len(csv_lines)} lines differ.")
        else:
            logging.info(f"{GREEN}✅ Line counts match.{RESET}")
    except Exception as e:
        logging.error(f"Mismatch check error: {e}")

# ------------------------- Main CLI ----------------------------- #
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Generate timestamp CSVs for karaoke_time_by_miguel.py")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    txt_path = Path(args.text)
    csv_name = sanitize_filename(txt_path.stem) + "_timestamps.csv"
    csv_path = LYRICS_DIR / csv_name
    mp3_path = SONGS_DIR / (sanitize_filename(args.title or txt_path.stem) + ".mp3")

    if mp3_path.exists():
        audio_path = mp3_path
    else:
        if str(args.audio).startswith(("http://", "https://")):
            audio_path = download_audio(args.audio, title_hint=args.title or txt_path.stem)
        else:
            audio_path = Path(args.audio).expanduser().resolve()

    if csv_path.exists() and validate_and_fix_csv(csv_path):
        warn_if_linecount_mismatch(txt_path, csv_path)
        return

    segs = whisperx_segments(audio_path, txt_path)
    if not segs:
        logging.error("💀 No segments returned.")
        return
    write_karaoke_csv(csv_path, txt_path, segs)
    warn_if_linecount_mismatch(txt_path, csv_path)
    logging.info(f"{GREEN}✅ Done → {csv_path}{RESET}")

# ------------------------- Faster-Whisper Mode ----------------------------- #
if __name__ == "__main__" and "--refresh-csv" in sys.argv:
    import argparse
    import ffmpeg, numpy as np, soundfile as sf
    from faster_whisper import WhisperModel

    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--refresh-csv", action="store_true")
    args, _ = ap.parse_known_args()

    def decode_audio_ffmpeg(path):
        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        (
            ffmpeg.input(str(path))
            .output(tmp_wav, format="wav", acodec="pcm_s16le", ac=1, ar="16000")
            .overwrite_output()
            .run(quiet=True)
        )
        audio, sr = sf.read(tmp_wav, dtype="float32")
        return audio, sr

    try:
        title = sanitize_filename(args.title)
        json_out = LYRICS_DIR / f"{title}_full.json"
        csv_out = LYRICS_DIR / f"{title}_timestamps.csv"

        model = WhisperModel("small", device="cpu", compute_type="float32")
        audio_data, sr = decode_audio_ffmpeg(Path(args.audio))
        segments, info = model.transcribe(audio_data, language="en")

        data = [{"start": seg.start, "end": seg.end, "text": seg.text.strip()} for seg in segments]
        json_out.write_text(json.dumps(data, indent=2), encoding="utf-8")

        txt_path = Path(args.text)
        lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        with csv_out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["line", "start"])
            for i, seg in enumerate(data):
                lyric = lines[i] if i < len(lines) else seg["text"]
                writer.writerow([lyric, f"{seg['start']:.3f}"])

        print(f"✅ Refreshed CSV + JSON → {csv_out.name}, {json_out.name}")
    except Exception as e:
        print(f"💀 Faster-Whisper refresh failed: {e}")
        sys.exit(1)

elif __name__ == "__main__":
    main()

# end of generate_timestamps.py
