#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# Auto-time lyrics (TXT) to audio (MP3/WAV):
# - Transcribes words with timestamps via faster-whisper
# - Aligns each lyric line to the best-matching word span
# - Emits timings/<slug>.csv with header: line_index,time_secs,text
#
# Repeated-lyrics aware:
# - Walks forward through the transcript once (no jumping back)
# - Only searches a local window ahead of the previous line
# - Penalizes matches far away from the expected time
#
# Usage:
#   python3 scripts/3_auto_timing.py --slug ascension \
#     --mp3 mp3s/ascension.mp3 --txt txts/ascension.txt \
#     --model-size small --lang en

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

# Optional pretty logging via rich (falls back to normal print)
try:
    from rich import print  # type: ignore
except Exception:  # pragma: no cover
    pass

# ---------------------------------------------------------------------
# Dependencies:
#   python3 -m pip install faster-whisper rapidfuzz
# ---------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel  # type: ignore
except Exception as e:  # pragma: no cover
    print("[bold red]Missing dependency:[/bold red] faster-whisper")
    print("  python3 -m pip install faster-whisper")
    raise

try:
    from rapidfuzz import fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:
    import difflib  # type: ignore
    _HAS_RAPIDFUZZ = False

try:
    import torch  # type: ignore
except Exception:  # pragma: no cover
    torch = None  # type: ignore

# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------- Data classes ----------
@dataclass
class Word:
    text: str
    start: float
    end: float


# ---------- Normalization helpers ----------
_PUNCT_RE = re.compile(r"[^a-z0-9'\s]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def norm(s: str) -> str:
    """
    Normalize text for fuzzy matching:
    - lowercase
    - keep apostrophes
    - strip other punctuation
    - collapse whitespace
    """
    s = s.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def load_lyrics_lines(txt_path: Path) -> List[str]:
    if not txt_path.exists():
        raise FileNotFoundError(f"TXT not found: {txt_path}")
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.rstrip("\n") for ln in raw.splitlines()]
    # Keep non-empty lines; tolerate extra whitespace
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
def choose_device(device_flag: Optional[str]) -> Tuple[str, str]:
    """
    Decide device and compute_type for faster-whisper.
    """
    if device_flag:
        dev = device_flag
    else:
        if torch is not None and getattr(torch, "cuda", None) and torch.cuda.is_available():  # type: ignore[attr-defined]
            dev = "cuda"
        else:
            dev = "cpu"

    if dev == "cuda":
        compute_type = "float16"
    else:
        compute_type = "int8"

    return dev, compute_type


def transcribe_words(
    audio_path: Path,
    model_size: str = "small",
    language: Optional[str] = None,
    device: Optional[str] = None,
) -> List[Word]:
    """
    Run faster-whisper and return a flat list of words with timestamps.
    """
    from time import perf_counter

    dev, compute_type = choose_device(device)
    print(
        f"[cyan]Loading faster-whisper model[/cyan] "
        f"size=[bold]{model_size}[/bold] device=[bold]{dev}[/bold] compute_type=[bold]{compute_type}[/bold]"
    )
    t0 = perf_counter()
    model = WhisperModel(model_size, device=dev, compute_type=compute_type)
    t1 = perf_counter()
    print(f"[cyan]Model loaded in {t1 - t0:.1f}s[/cyan]")

    print(f"[cyan]Transcribing audio:[/cyan] {audio_path}")
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        word_timestamps=True,
        language=language,
    )

    words: List[Word] = []
    for seg in segments:
        if getattr(seg, "words", None):
            for w in seg.words:
                if w.start is None or w.end is None:
                    continue
                wt = (w.word or "").strip()
                if not wt:
                    continue
                words.append(Word(text=wt, start=float(w.start), end=float(w.end)))
        else:
            # Fallback: treat whole segment as one token if no word timestamps
            if seg.start is None:
                continue
            seg_text = (seg.text or "").strip()
            if not seg_text:
                continue
            words.append(Word(text=seg_text, start=float(seg.start), end=float(seg.end or seg.start + 2.0)))

    print(f"[green]Transcribed {len(words)} words[/green]")
    return words


# ---------- Similarity & alignment ----------
def _similarity(a: str, b: str) -> float:
    """
    Return a similarity score in roughly [0, 100].
    """
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return float(fuzz.ratio(a, b))
    # Fallback to difflib
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0  # type: ignore[name-defined]


def align_lyrics_to_words(lines: List[str], words: List[Word]) -> List[Tuple[int, float, str]]:
    """
    Repeated-lyrics-aware alignment.

    Strategy:
    - Pre-normalize transcript words.
    - Track a "search_start_idx" that only moves forward (never backwards).
    - For each lyric line:
        * Look at a limited window of words ahead of search_start_idx.
        * Score each window by (text_similarity - time_penalty).
        * Choose the best start index.
        * The line's time is the start time of that window.
    - Time penalty pulls matches toward the expected time (prev_time + avg_gap),
      so identical repeated lines near the end don't steal lines that belong
      to earlier repeats.
    """
    if not words:
        # Fallback: dumb linear guess
        print("[yellow]No words from transcription; using naive +2.5s spacing[/yellow]")
        out: List[Tuple[int, float, str]] = []
        t = 0.0
        for idx, line in enumerate(lines):
            out.append((idx, t, line))
            t += 2.5
        return out

    n_words = len(words)
    words_norm = [norm(w.text) for w in words]

    # Overall track span; used to estimate average gap between lyric lines
    total_span = max(0.1, words[-1].end - words[0].start)
    avg_gap = max(1.5, min(6.0, total_span / max(1, len(lines))))  # seconds per line, clamped

    # How far ahead (in word tokens) each line is allowed to search.
    # This is the main guard against jumping to a very late repetition.
    MAX_LOOKAHEAD_WORDS = 120  # ~30–60 seconds of audio depending on speech rate

    # Time penalty scales
    TIME_PENALTY_PER_SEC = 1.3   # how many "similarity points" per second of distance
    TIME_PENALTY_MAX = 40.0      # cap penalty so far matches aren't completely nuked

    triples: List[Tuple[int, float, str]] = []

    # We'll walk forward through the transcript once.
    search_start_idx = 0
    prev_time = max(0.0, words[0].start - 0.5)

    for idx, raw_line in enumerate(lines):
        line_n = norm(raw_line)
        if not line_n:
            # Blank line: just keep time monotone-ish
            t = prev_time + 0.5
            triples.append((idx, t, raw_line))
            prev_time = t
            continue

        line_tokens = line_n.split()
        # Rough estimate of how many words should match this line
        approx_len = max(1, min(len(line_tokens), 12))

        best_score = -1e9
        best_start = search_start_idx

        # Limit search window to avoid leaping across the song
        start_min = min(search_start_idx, n_words - 1)
        start_max = min(n_words - 1, start_min + MAX_LOOKAHEAD_WORDS)

        if idx == 0:
            expected_time = words[0].start
        else:
            expected_time = prev_time + avg_gap

        for start in range(start_min, start_max + 1):
            # Window size heuristics: not too small, not too large
            remaining = n_words - start
            if remaining <= 0:
                break

            max_window = min(
                remaining,
                len(line_tokens) + 4,
                approx_len + 6,
            )

            window_norm = " ".join(words_norm[start : start + max_window]).strip()
            if not window_norm:
                continue

            text_sim = _similarity(window_norm, line_n)  # 0..100-ish

            # Time penalty: earlier lines expect matches near expected_time
            start_time = words[start].start
            if expected_time is not None:
                time_diff = abs(start_time - expected_time)
                time_penalty = min(time_diff * TIME_PENALTY_PER_SEC, TIME_PENALTY_MAX)
            else:
                time_penalty = 0.0

            score = text_sim - time_penalty

            if score > best_score:
                best_score = score
                best_start = start

        # Threshold for "this line matched something"
        if best_score < 30.0:
            # Couldn't confidently align; guess based on previous time
            t = prev_time + avg_gap
            print(
                f"[yellow]Low alignment score ({best_score:.1f}) for line {idx} → "
                f"fallback at {t:.2f}s[/yellow]"
            )
        else:
            t_candidate = words[best_start].start
            # Enforce monotonic increasing times with a small margin
            if t_candidate < prev_time - 0.25:
                t_candidate = prev_time + 0.01
            t = t_candidate

        triples.append((idx, t, raw_line))
        prev_time = t

        # Advance the search window so future lines can't reuse the same words
        # and can't jump backwards.
        MIN_ADVANCE_WORDS = max(3, len(line_tokens))
        search_start_idx = min(n_words - 1, max(best_start + MIN_ADVANCE_WORDS, search_start_idx + 1))

    return triples


# ---------- CLI ----------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-time lyrics TXT to audio using faster-whisper.")

    parser.add_argument(
        "--slug",
        required=True,
        help="Song slug (used for timings/<slug>.csv).",
    )
    parser.add_argument(
        "--mp3",
        type=str,
        help="Path to audio file. Default: mp3s/<slug>.mp3",
    )
    parser.add_argument(
        "--txt",
        type=str,
        help="Path to lyrics TXT. Default: txts/<slug>.txt",
    )
    parser.add_argument(
        "--model-size",
        type=str,
        default="small",
        help="faster-whisper model size (tiny, base, small, medium, large-v2, etc.).",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=None,
        help="Language code (e.g., en, es). None = auto-detect.",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Device for faster-whisper: cpu or cuda. Default: auto-detect.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    slug = args.slug
    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    audio_path = Path(args.mp3) if args.mp3 else (MP3_DIR / f"{slug}.mp3")

    print(f"[cyan]Slug:[/cyan] {slug}")
    print(f"[cyan]TXT:[/cyan]  {txt_path}")
    print(f"[cyan]MP3:[/cyan]  {audio_path}")

    if not audio_path.exists():
        print(f"[bold red]Audio not found:[/bold red] {audio_path}")
        sys.exit(1)
    if not txt_path.exists():
        print(f"[bold red]TXT not found:[/bold red] {txt_path}")
        sys.exit(1)

    lines = load_lyrics_lines(txt_path)
    print(f"[green]Loaded {len(lines)} lyric lines[/green]")

    words = transcribe_words(
        audio_path=audio_path,
        model_size=args.model_size,
        language=args.lang,
        device=args.device,
    )

    triples = align_lyrics_to_words(lines, words)
    write_timings_csv(slug, triples)


if __name__ == "__main__":
    main()
# end of 3_auto_timing.py
