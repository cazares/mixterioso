#!/usr/bin/env python3
# scripts/3_auto_timing.py
# Auto-time lyrics (TXT) to audio (MP3/WAV/…):
# - Transcribes words with timestamps via faster-whisper
# - Aligns each lyric line to the best word span
# - Emits timings/<slug>.csv with header: line_index,time_secs,text
#
# Usage:
#   python3 scripts/3_auto_timing.py --slug californication \
#     --mp3 mp3s/californication.mp3 --txt txts/californication.txt \
#     --model-size small --lang en
#
# Notes:
# - Designed to be callable from a future REST API (functions below).
# - Works on macOS/MacinCloud (no system TTS needed). Requires ffmpeg.

from __future__ import annotations
import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Tuple, Optional

# Optional pretty logging
try:
    from rich import print  # type: ignore
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------------
# Dependencies:
#   python3 -m pip install faster-whisper rapidfuzz
#   (rapidfuzz optional; will fallback to difflib)
# ---------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception as e:
    print("[bold red]Missing dependency:[/bold red] faster-whisper")
    print("  python3 -m pip install faster-whisper")
    raise

try:
    from rapidfuzz import fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:
    import difflib
    _HAS_RAPIDFUZZ = False


# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)


# ---------- Data ----------
@dataclass
class Word:
    text: str
    start: float
    end: float
    score: float


# ---------- Helpers ----------
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w']+")

def norm(s: str) -> str:
    # preserve apostrophes for contractions, strip other punct, lowercase
    s = s.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s

def load_lyrics_lines(txt_path: Path) -> List[str]:
    if not txt_path.exists():
        raise FileNotFoundError(f"TXT not found: {txt_path}")
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    # Keep non-empty lines; tolerate extra whitespace
    lines = [ln.strip() for ln in raw.splitlines()]
    return [ln for ln in lines if ln.strip()]

def write_timings_csv(slug: str, triples: List[Tuple[int, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "time_secs", "text"])
        for li, ts, tx in triples:
            w.writerow([li, f"{ts:.3f}", tx])
    print(f"[green]Wrote timings:[/green] {out} ({len(triples)} rows)")
    return out


# ---------- Transcription ----------
def transcribe_words(
    audio_path: Path,
    model_size: str = "small",
    language: Optional[str] = None,
    device: Optional[str] = None,
    vad: bool = True,
) -> List[Word]:
    """
    Return a flat list of word-level timestamps using faster-whisper.
    """
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))

    # Device auto-pick: metal on Apple Silicon if available; else cpu
    if device is None:
        device = "auto"  # faster-whisper will pick best backend

    print(f"[cyan]Transcribing words with faster-whisper[/cyan] | model={model_size} lang={language or 'auto'} device={device}")
    model = WhisperModel(model_size, device=device, compute_type="auto")

    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=vad,
        word_timestamps=True,
        beam_size=5,
        best_of=5,
        temperature=0.0,
    )

    words: List[Word] = []
    for seg in segments:
        for w in seg.words or []:
            if w.word is None:
                continue
            # faster-whisper gives .start, .end, .word, .prob
            text = (w.word or "").strip()
            if not text:
                continue
            words.append(Word(text=text, start=float(w.start), end=float(w.end), score=float(getattr(w, "probability", 0.0))))
    # Keep increasing time only
    words = [w for w in words if w.start is not None and w.end is not None and w.end >= w.start]
    print(f"[green]Transcribed words:[/green] {len(words)}")
    return words


# ---------- Alignment ----------
def _ratio(a: str, b: str) -> float:
    if _HAS_RAPIDFUZZ:
        # rapidfuzz ratio is [0..100]
        return float(fuzz.ratio(a, b)) / 100.0
    else:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio()

def _window_score(window_text: str, target_line: str) -> float:
    # Weighted score favors longer overlaps
    r = _ratio(window_text, target_line)
    # Bonus for inclusion matches
    if target_line and target_line in window_text:
        r = max(r, 0.98)
    return r

def align_lines_to_words(
    lyric_lines: List[str],
    words: List[Word],
    max_window_words: int = 20,
    min_score: float = 0.45,
) -> List[Tuple[int, float, str]]:
    """
    For each lyric line, find the best consecutive word window.
    Returns list of (line_index, time_secs, text).
    time_secs = start time of the best window (or median of window).
    """
    # Build normalized word list we can join
    norm_words = [norm(w.text) for w in words]
    # Also keep original tokens and timestamps in sync
    results: List[Tuple[int, float, str]] = []

    # Precompute cumulative mid-times for median picks
    mid_times = [0.5 * (w.start + w.end) for w in words]

    # Quick index: join small spans into strings for scoring
    # We'll sweep a sliding window for each target line
    N = len(words)
    for li, line in enumerate(lyric_lines):
        target = norm(line)
        if not target:
            # still emit a rubber-stamp time (next known time) to keep index continuity
            t = words[0].start if words else 0.0
            results.append((li, float(t), line))
            continue

        best_s = -1.0
        best_i = 0
        best_j = 0

        # Heuristic window bounds: try window sizes from 3 up to max_window_words
        # If target is short, smaller window; if long, larger
        target_len = max(3, min(max_window_words, max(3, len(target.split()))))
        w_min = max(3, min(12, target_len))   # lower window
        w_max = max(w_min, min(max_window_words, target_len + 6))

        for wsize in range(w_min, w_max + 1):
            # Slide over all windows of size wsize
            j_end = N - wsize
            for i in range(0, max(0, j_end + 1)):
                j = i + wsize
                # Fast cut: first/last token presence heuristic
                window_tokens = norm_words[i:j]
                joined = " ".join(window_tokens)
                s = _window_score(joined, target)
                if s > best_s:
                    best_s, best_i, best_j = s, i, j

        if best_s < min_score:
            # Fallback: try to find single anchor word inside target
            anchor_idx = -1
            anchor_score = -1.0
            target_tokens = target.split()
            token_set = set(target_tokens)
            for i, tok in enumerate(norm_words):
                if tok in token_set:
                    # small bonus for matching token
                    sc = 0.5 + 0.5 * (len(tok) / max(1, len(target)))
                    if sc > anchor_score:
                        anchor_score = sc
                        anchor_idx = i
            if anchor_idx >= 0:
                # set time to its mid
                t = mid_times[anchor_idx]
                results.append((li, float(t), line))
            else:
                # Give up gracefully: monotonic non-decreasing time
                t = results[-1][1] + 0.3 if results else (words[0].start if words else 0.0)
                results.append((li, float(t), line))
            continue

        # Pick a representative time for the window [best_i, best_j)
        # Use median of mid_times in window (robust vs outliers)
        window_mids = mid_times[best_i:best_j]
        if window_mids:
            mid_sorted = sorted(window_mids)
            m = len(mid_sorted)
            if m % 2 == 1:
                t_mid = mid_sorted[m // 2]
            else:
                t_mid = 0.5 * (mid_sorted[m // 2 - 1] + mid_sorted[m // 2])
        else:
            t_mid = words[best_i].start if best_i < len(words) else (results[-1][1] + 0.3 if results else 0.0)

        # Append
        results.append((li, float(t_mid), line))

    # Ensure non-decreasing timestamps (tiny smoothing)
    last = 0.0
    out_fixed: List[Tuple[int, float, str]] = []
    for li, ts, tx in results:
        ts = max(ts, last)
        out_fixed.append((li, ts, tx))
        last = ts

    return out_fixed


# ---------- CLI ----------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-time lyrics to audio and emit timings CSV.")
    p.add_argument("--slug", required=True, help="Song slug (used for output CSV name).")
    p.add_argument("--mp3", required=True, help="Path to audio file (mp3/wav/m4a…).")
    p.add_argument("--txt", required=True, help="Path to lyrics .txt (one line per lyric).")
    p.add_argument("--model-size", default="small", help="Whisper model size (tiny/base/small/medium/large-v2).")
    p.add_argument("--lang", default=None, help="Force language code (e.g., en, es). Default: auto.")
    p.add_argument("--device", default=None, help="faster-whisper device: auto|cpu|cuda|metal")
    p.add_argument("--min-score", type=float, default=0.45, help="Min alignment score to accept (0..1).")
    p.add_argument("--max-window", type=int, default=20, help="Max words per window for matching.")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    slug = args.slug.strip().lower().replace(" ", "_")
    audio_path = Path(args.mp3)
    txt_path = Path(args.txt)

    print(f"[bold cyan]Auto-timing[/bold cyan] slug={slug}")
    print(f"  audio: {audio_path}")
    print(f"  lyrics: {txt_path}")

    lines = load_lyrics_lines(txt_path)
    if not lines:
        print("[red]No lyric lines found[/red]")
        return 2

    words = transcribe_words(
        audio_path=audio_path,
        model_size=args.model_size,
        language=args.lang,
        device=args.device,
    )
    if not words:
        print("[red]No words recognized[/red]")
        return 3

    triples = align_lines_to_words(
        lyric_lines=lines,
        words=words,
        max_window_words=max(6, min(args.max_window, 40)),
        min_score=max(0.0, min(args.min_score, 0.99)),
    )
    write_timings_csv(slug, triples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# end of 3_auto_timing.py
