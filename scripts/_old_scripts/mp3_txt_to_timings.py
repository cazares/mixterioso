#!/usr/bin/env python3
# scripts/mp3_txt_to_timings.py
#
# One-shot: MP3 + TXT  -> timings/{slug}.csv   (line_index,time_secs,text)
# Steps:
#   1) Optional Demucs separation (default: htdemucs_6s) to isolate vocals
#   2) Faster-Whisper word-level timestamps (default: VAD OFF for vocals-only)
#   3) Greedy alignment of lyric lines to ASR words
#
# Works on plain CPU (Mac/MacInCloud). Demucs + Faster-Whisper must be installed.
#
# Usage (recommended):
#   python3 scripts/mp3_txt_to_timings.py \
#     --mp3 mp3s/around_the_world_snip.mp3 \
#     --txt txts/around_the_world_snip.txt \
#     --slug around_the_world_snip \
#     --demucs-model htdemucs_6s \
#     --model-size small \
#     --no-vad
#
# Quick run without Demucs (e.g., to iterate fast):
#   python3 scripts/mp3_txt_to_timings.py --no-demucs ... (same flags)
#
# Output:
#   timings/{slug}.csv  with header: line_index,time_secs,text

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent.parent
MP3_DIR = BASE_DIR / "mp3s"
TXT_DIR = BASE_DIR / "txts"
TIMINGS_DIR = BASE_DIR / "timings"
STEMS_DIR = BASE_DIR / "stems"  # demucs output root

# ---------- Logging ----------
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"

def log(tag: str, msg: str, color: str = RESET) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}")

# ---------- Small utils ----------
def slugify(text: str) -> str:
    s = text.strip().lower()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w\-]", "", s)
    return s or "song"

def ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path)
        ], stderr=subprocess.STDOUT, text=True).strip()
        return float(out)
    except Exception as e:
        log("FFPROBE", f"Failed to probe duration for {path}: {e}", YELLOW)
        return 0.0

def read_lyrics_lines(txt_path: Path) -> List[str]:
    lines = []
    for raw in txt_path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s:
            lines.append(s)
    return lines

# ---------- Demucs ----------
def extract_vocals_demucs(mp3_path: Path, model: str = "htdemucs_6s") -> Path:
    """
    Runs demucs and returns path to vocals stem (mp3 preferred, else wav).
    With 6-stem models, do NOT pass --two-stems.
    """
    STEMS_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "demucs.separate", "-n", model, "--mp3", "-o", str(STEMS_DIR), str(mp3_path)]
    # For non-6s models, speeding up with two-stems vocals is okay:
    if not model.endswith("_6s"):
        cmd = [sys.executable, "-m", "demucs.separate", "-n", model, "--two-stems", "vocals", "--mp3", "-o", str(STEMS_DIR), str(mp3_path)]

    log("DEMUX", " ".join(cmd), CYAN)
    subprocess.run(cmd, check=True)
    # demucs output tree: stems/<model>/<track_basename>/vocals.(mp3|wav)
    base = mp3_path.stem
    model_dir = STEMS_DIR / model
    if not model_dir.exists():
        # some demucs versions nest differently; search recursively
        candidates = list(STEMS_DIR.rglob(f"{base}/vocals.*"))
    else:
        candidates = list((model_dir / base).glob("vocals.*"))
        if not candidates:
            candidates = list(model_dir.rglob(f"{base}/vocals.*"))

    # Prefer mp3, then wav
    mp3s = [p for p in candidates if p.suffix.lower() == ".mp3"]
    wavs = [p for p in candidates if p.suffix.lower() == ".wav"]
    if mp3s:
        vpath = mp3s[0]
    elif wavs:
        vpath = wavs[0]
    else:
        raise FileNotFoundError(f"Could not find vocals stem for {mp3_path} in {STEMS_DIR} (model {model}).")

    log("DEMUX", f"Vocals stem: {vpath}", GREEN)
    return vpath

# ---------- Faster-Whisper ----------
@dataclass
class Word:
    text: str
    start: float
    end: float

def faster_whisper_words(
    audio_path: Path,
    model_size: str = "small",
    lang: str = "en",
    use_vad: bool = False,
    device: str = "auto",
) -> List[Word]:
    log("ASR", f"Transcribing words with faster-whisper | model={model_size} lang={lang or 'auto'} vad={use_vad} device={device}", CYAN)
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type="auto")
    segments, _info = model.transcribe(
        str(audio_path),
        language=(lang or None),
        vad_filter=use_vad,
        word_timestamps=True,
        beam_size=5,
        temperature=0.0,
        no_speech_threshold=0.35,
        condition_on_previous_text=True,
    )
    words: List[Word] = []
    for seg in segments:
        if not getattr(seg, "words", None):
            continue
        for w in seg.words:
            if w and w.start is not None and w.end is not None and w.word:
                words.append(Word(text=w.word, start=float(w.start), end=float(w.end)))
    log("ASR", f"Collected {len(words)} words", GREEN)
    return words

# ---------- Alignment ----------
_WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)

def norm_tokens(s: str) -> List[str]:
    return _WORD_RE.findall(s.lower())

def greedy_line_alignment(
    lines: List[str],
    words: List[Word],
    min_ratio: float = 0.55,
    search_pad: int = 48,
) -> List[Tuple[int, float, float, str]]:
    """
    Map each lyric line to a timestamp (start of best-matching word span).
    - Token-based matching against ASR words (normalized).
    - Greedy, monotonic: each line searches forward from last match.
    - Score = (# lyric tokens present in window) / (# lyric tokens)
    Returns list of triplets: (line_index, time_secs, text).
    """
    if not lines:
        return []
    if not words:
        return []

    word_tokens = [t for w in words for t in norm_tokens(w.text)]
    # map word index -> its time (use start time of first token in the same word)
    word_times = []
    for w in words:
        toks = norm_tokens(w.text)
        if not toks:
            continue
        # Assign time to each token occurrence
        for _ in toks:
            word_times.append(w.start)

    out: List[Tuple[int, float, float, str]] = []
    cursor = 0
    N = len(word_tokens)

    for li, line in enumerate(lines):
        ltoks = norm_tokens(line)
        if not ltoks:
            # Empty after normalization: set same time as previous or 0.0
            ts = out[-1][1] if out else 0.0
            out.append((li, ts, line))
            continue

        best = (-1.0, None, None)  # (score, j, k)
        approx_len = max(1, len(ltoks))
        # Window from cursor up to cursor + search_pad
        j_start = cursor
        j_end = min(N - 1, cursor + search_pad)

        # evaluate variable window sizes around lyric length
        for j in range(j_start, j_end + 1):
            k_max = min(N, j + approx_len + (approx_len // 2) + 1)
            # Try k from j+len.. up to k_max
            for k in range(min(N, j + approx_len), k_max):
                window = word_tokens[j:k]
                if not window:
                    continue
                # simple coverage ratio
                hits = 0
                window_set = set(window)
                for t in ltoks:
                    if t in window_set:
                        hits += 1
                ratio = hits / float(len(ltoks))
                if ratio > best[0]:
                    best = (ratio, j, k)

        score, j, k = best
        if score >= min_ratio and j is not None:
            # timestamp = start time of first token in chosen window
            ts = word_times[j] if j < len(word_times) else (out[-1][1] if out else 0.0)
            out.append((li, ts, line))
            cursor = max(cursor, (k or j + 1))
        else:
            # Fallback: monotonic ramp from previous time
            prev = out[-1][1] if out else 0.0
            ts = prev + 1.75  # conservative gap
            out.append((li, ts, line))
            cursor = min(N - 1, cursor + approx_len)

    # Enforce non-decreasing times (strictly increasing by tiny epsilon)
    eps = 1e-3
    last = -1e9
    fixed: List[Tuple[int, float, float, str]] = []
    for li, ts, line in out:
        if ts <= last:
            ts = last + eps
        fixed.append((li, ts, line))
        last = ts
    return fixed

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(description="MP3+TXT -> timings CSV via Demucs + Faster-Whisper + greedy alignment")
    ap.add_argument("--mp3", required=True, help="Path to input MP3")
    ap.add_argument("--txt", required=True, help="Path to lyrics TXT (one line per lyric)")
    ap.add_argument("--slug", required=True, help="Slug name for output timings CSV")
    ap.add_argument("--out-dir", default=str(TIMINGS_DIR), help="Output directory for timings CSV")
    ap.add_argument("--demucs-model", default="htdemucs_6s", help="Demucs model (htdemucs_6s|htdemucs_ft|htdemucs|mdx_extra, etc.)")
    ap.add_argument("--no-demucs", action="store_true", help="Skip Demucs; run ASR on original MP3")
    ap.add_argument("--model-size", default="small", help="Faster-Whisper size: tiny/base/small/medium/large-v3")
    ap.add_argument("--lang", default="en", help="Language hint (e.g. en). Empty = auto")
    ap.add_argument("--no-vad", action="store_true", help="Disable VAD (recommended for isolated vocals)")
    ap.add_argument("--min-ratio", type=float, default=0.55, help="Min coverage ratio to accept a match")
    ap.add_argument("--search-pad", type=int, default=48, help="How far ahead in ASR tokens to search per line")
    ap.add_argument("--device", default="auto", help="Faster-Whisper device: auto|cpu|cuda")
    ap.add_argument("--debug-json", action="store_true", help="Emit debug JSON next to CSV (matched indices, times)")
    args = ap.parse_args()

    mp3_path = Path(args.mp3)
    txt_path = Path(args.txt)
    slug = slugify(args.slug)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not mp3_path.exists():
        log("ARGS", f"MP3 not found: {mp3_path}", RED); sys.exit(2)
    if not txt_path.exists():
        log("ARGS", f"TXT not found: {txt_path}", RED); sys.exit(2)

    log("RUN", f"slug={slug}", CYAN)
    log("RUN", f"mp3={mp3_path}", CYAN)
    log("RUN", f"txt={txt_path}", CYAN)

    # 1) Demucs vocals (optional)
    if args.no_demucs:
        vocals = mp3_path
        log("DEMUX", "Skipping Demucs; using original MP3 for ASR", YELLOW)
    else:
        vocals = extract_vocals_demucs(mp3_path, model=args.demucs_model)

    # 2) ASR words
    words = faster_whisper_words(
        vocals,
        model_size=args.model_size,
        lang=args.lang,
        use_vad=(not args.no_vad),
        device=args.device,
    )

    # 3) Load lyrics & align
    lines = read_lyrics_lines(txt_path)
    log("LYRICS", f"{len(lines)} non-empty lines loaded", GREEN)

    if not words:
        log("ASR", "No words recognized; falling back to linear spacing.", YELLOW)
        dur = ffprobe_duration(vocals) or ffprobe_duration(mp3_path) or 60.0
        gap = max(0.5, dur / max(1, len(lines)))
        triples = [(i, i * gap, line) for i, line in enumerate(lines)]
    else:
        aligned = greedy_line_alignment(lines, words, min_ratio=args.min_ratio, search_pad=args.search_pad)
        triples = aligned  # (line_index, time_secs, text)

    # 4) Write CSV
    out_csv = out_dir / f"{slug}.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "time_secs", "text"])
        for li, ts, line in triples:
            w.writerow([li, f"{ts:.3f}", line])
    log("OUT", f"Wrote {out_csv}", GREEN)

    # Optional debug JSON
    if args.debug_json:
        dj = {
            "slug": slug,
            "mp3": str(mp3_path),
            "txt": str(txt_path),
            "triples": [{"line_index": li, "start_secs": ts, "end_secs": te, "text": line} for li, ts, te, line in triples],
        }
        dbg_path = out_dir / f"{slug}.align.debug.json"
        dbg_path.write_text(json.dumps(dj, indent=2), encoding="utf-8")
        log("OUT", f"Wrote {dbg_path}", CYAN)

    print(out_csv)

if __name__ == "__main__":
    main()
# end of mp3_txt_to_timings.py
