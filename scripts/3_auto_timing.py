#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# Auto-time lyrics (TXT) to audio (MP3/WAV):
# - Transcribes words with timestamps via faster-whisper
# - Aligns each lyric line to the best-matching word span
# - Emits timings/<slug>.csv with header: line_index,start,end,text
#
# Repeated-lyrics aware:
# - Walks forward through the transcript once (no jumping back)
# - Only searches a local window ahead of the previous line
# - Penalizes matches far away from the expected time
# - Post-fix pass smooths timings for consecutive repeated lines
#
# Usage:
#   python3 scripts/3_auto_timing.py --slug ascension \
#     --mp3 mp3s/ascension.mp3 --txt txts/ascension.txt \
#     --model-size small --lang en
#
# Near-term enhancement:
#   - If no explicit --mp3 is given, this script will prefer a vocal-only
#     stem from separated/*/<slug>/*vocals*.wav (Demucs output) when present,
#     falling back to mp3s/<slug>.mp3 otherwise.

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

SEPARATED_DIR = BASE_DIR / "separated"  # where Demucs stems live

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


def write_timings_csv(slug: str, triples: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, ts, te, tx in triples:
            w.writerow([li, f"{ts:.3f}", f"{te:.3f}", tx])
    print(f"[green]Wrote timings:[/green] {out} ({len(triples)} rows)")
    return out


# ---------- Audio selection for timing ----------
def choose_timing_audio(slug: str, explicit_audio: Optional[Path]) -> Path:
    """
    Decide which audio file to use for timing:
      - If explicit_audio is provided and exists: use it.
      - Else, search for a Demucs vocal stem:
            separated/*/<slug>/*vocals*.wav
        pick the newest such file.
      - Else, fall back to mp3s/<slug>.mp3.
    """
    # 1) Explicit override from CLI
    if explicit_audio is not None:
        if explicit_audio.exists():
            print(f"[cyan]Using explicit audio for timing:[/cyan] {explicit_audio}")
            return explicit_audio
        else:
            print(
                f"[yellow]Explicit --mp3 not found, falling back to stems/mp3 search:[/yellow] "
                f"{explicit_audio}"
            )

    # 2) Try to find a Demucs vocal stem
    candidates: List[Path] = []
    if SEPARATED_DIR.exists():
        for model_dir in SEPARATED_DIR.iterdir():
            if not model_dir.is_dir():
                continue
            slug_dir = model_dir / slug
            if not slug_dir.is_dir():
                continue
            # Demucs typically names as 'vocals.wav' or '*vocals*.wav'
            for p in slug_dir.glob("*vocals*.wav"):
                candidates.append(p)

    if candidates:
        # Pick newest candidate by mtime = "latest processed"
        best = max(candidates, key=lambda p: p.stat().st_mtime)
        print(f"[green]Using vocal stem for timing:[/green] {best}")
        return best

    # 3) Fallback to original MP3
    audio_path = MP3_DIR / f"{slug}.mp3"
    if audio_path.exists():
        print(
            f"[yellow]Vocal stem not found; using original mp3 for timing:[/yellow] "
            f"{audio_path}"
        )
        return audio_path

    print(f"[bold red]No audio found for timing for slug={slug}[/bold red]")
    sys.exit(1)


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


def _postfix_fix_repeated_runs(
    lines: List[str],
    triples: List[Tuple[int, float, float, str]],
    words: List[Word],
    avg_gap: float,
) -> List[Tuple[int, float, float, str]]:
    """
    Post-fix pass: smooth timings for consecutive repeated lines.

    For any maximal run of consecutive lines whose normalized texts are identical,
    if the gaps inside that run look suspicious (too small, too big, or non-increasing),
    we keep the first start as an anchor and re-space the run evenly between that
    start and the next non-repeated line (or track end).
    """
    if not triples:
        return triples

    n = len(triples)
    norm_lines = [norm(t) for (_li, _s, _e, t) in triples]
    track_end = words[-1].end if words else max(triples[-1][2], triples[-1][1] + avg_gap)

    # Helper to decide if a run "needs fixing"
    def needs_fix(start_idx: int, end_idx: int) -> bool:
        if end_idx <= start_idx:
            return False
        diffs: List[float] = []
        for i in range(start_idx, end_idx):
            _, s1, _e1, _t1 = triples[i]
            _, s2, _e2, _t2 = triples[i + 1]
            diffs.append(s2 - s1)
        if not diffs:
            return False

        min_gap = min(diffs)
        max_gap = max(diffs)

        # Suspicious if any non-increasing, or gaps wildly off from avg_gap
        if any(d <= 0 for d in diffs):
            return True
        if max_gap > 2.5 * avg_gap:
            return True
        if min_gap < 0.35 * avg_gap:
            return True
        return False

    i = 0
    while i < n:
        base = norm_lines[i]
        if not base:
            i += 1
            continue

        # Find maximal run of same normalized text
        j = i
        while j + 1 < n and norm_lines[j + 1] == base:
            j += 1

        if j == i:
            i += 1
            continue  # no repetition

        # We have a run [i, j] (inclusive) of repeated lines
        if not needs_fix(i, j):
            i = j + 1
            continue

        # Anchor at the first start
        li0, start0, end0, text0 = triples[i]
        run_len = j - i + 1

        # Right boundary is next line start, or track_end if none
        if j + 1 < n:
            _, next_start, _next_end, _next_text = triples[j + 1]
            right_bound = max(next_start, start0 + avg_gap * run_len * 0.7)
        else:
            right_bound = max(track_end, start0 + avg_gap * run_len * 0.7)

        total_window = max(right_bound - start0, avg_gap * run_len * 0.7)
        # Use a gap close to avg_gap but respecting the available window
        ideal_gap = min(avg_gap * 1.2, total_window / (run_len + 0.5))
        ideal_gap = max(avg_gap * 0.5, ideal_gap)

        # Re-space starts inside the window
        for k in range(run_len):
            idx = i + k
            li, _old_s, old_e, text = triples[idx]
            # Preserve a hint of original duration but keep sensible
            orig_dur = max(0.25, old_e - start0)
            new_start = start0 + k * ideal_gap
            new_end = new_start + min(orig_dur, ideal_gap * 0.9)

            if new_end > right_bound - 0.05:
                new_end = right_bound - 0.05
            if new_end <= new_start:
                new_end = new_start + 0.05

            triples[idx] = (li, new_start, new_end, text)

        i = j + 1

    # Final safety: ensure end times do not exceed next start
    for i in range(len(triples) - 1):
        li, s, e, t = triples[i]
        _li2, s_next, _e2, _t2 = triples[i + 1]
        if e > s_next:
            e = max(s, s_next - 0.05)
        triples[i] = (li, s, e, t)

    return triples


def align_lyrics_to_words(lines: List[str], words: List[Word]) -> List[Tuple[int, float, float, str]]:
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
    - After the initial pass, a post-fix layer re-spaces any suspicious
      runs of consecutive repeated lines.
    """
    if not words:
        # Fallback: dumb linear guess
        print("[yellow]No words from transcription; using naive +2.5s spacing[/yellow]")
        out: List[Tuple[int, float, float, str]] = []
        t = 0.0
        for idx, line in enumerate(lines):
            out.append((idx, t, t + 2.5, line))
            t += 2.5
        return out

    n_words = len(words)
    words_norm = [norm(w.text) for w in words]

    # Overall track span; used to estimate average gap between lyric lines
    total_span = max(0.1, words[-1].end - words[0].start)
    avg_gap = max(1.5, min(6.0, total_span / max(1, len(lines))))  # seconds per line, clamped

    # How far ahead (in word tokens) each line is allowed to search.
    MAX_LOOKAHEAD_WORDS = 120  # ~30–60 seconds of audio depending on speech rate

    # Time penalty scales
    TIME_PENALTY_PER_SEC = 1.3   # how many "similarity points" per second of distance
    TIME_PENALTY_MAX = 40.0      # cap penalty so far matches aren't completely nuked

    triples: List[Tuple[int, float, float, str]] = []

    # We'll walk forward through the transcript once.
    search_start_idx = 0
    prev_time = max(0.0, words[0].start - 0.5)

    for idx, raw_line in enumerate(lines):
        line_n = norm(raw_line)
        if not line_n:
            # Blank line: keep a short gap
            t_start = prev_time + 0.5
            t_end = t_start + 2.0
            triples.append((idx, t_start, t_end, raw_line))
            prev_time = t_start
            continue

        line_tokens = line_n.split()
        approx_len = max(1, min(len(line_tokens), 12))

        best_score = -1e9
        best_start = search_start_idx

        start_min = min(search_start_idx, n_words - 1)
        start_max = min(n_words - 1, start_min + MAX_LOOKAHEAD_WORDS)

        if idx == 0:
            expected_time = words[0].start
        else:
            expected_time = prev_time + avg_gap

        for start in range(start_min, start_max + 1):
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

        if best_score < 30.0:
            # Couldn't confidently align; guess based on previous time
            t_start = prev_time + avg_gap
            print(
                f"[yellow]Low alignment score ({best_score:.1f}) for line {idx} → "
                f"fallback at {t_start:.2f}s[/yellow]"
            )
        else:
            t_candidate = words[best_start].start
            if t_candidate < prev_time - 0.25:
                t_candidate = prev_time + 0.01
            t_start = t_candidate

        # End time heuristic: either the start of the next aligned line, or start + avg_gap
        t_end = t_start + avg_gap

        triples.append((idx, t_start, t_end, raw_line))
        prev_time = t_start

        MIN_ADVANCE_WORDS = max(3, len(line_tokens))
        search_start_idx = min(
            n_words - 1,
            max(best_start + MIN_ADVANCE_WORDS, search_start_idx + 1),
        )

    # First pass: clamp end times so they don't exceed the next line's start
    for i in range(len(triples) - 1):
        li, s, e, text = triples[i]
        _li2, s_next, _e2, _t2 = triples[i + 1]
        if e > s_next:
            e = max(s, s_next - 0.05)
        triples[i] = (li, s, e, text)

    # Post-fix: refine consecutive repeated lines
    triples = _postfix_fix_repeated_runs(lines, triples, words, avg_gap)

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
        help="Path to audio file. If omitted, will prefer a vocal stem from separated/*/<slug>/*vocals*.wav, "
             "falling back to mp3s/<slug>.mp3.",
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
    explicit_audio = Path(args.mp3) if args.mp3 else None
    audio_path = choose_timing_audio(slug, explicit_audio)

    print(f"[cyan]Slug:[/cyan]  {slug}")
    print(f"[cyan]TXT:[/cyan]   {txt_path}")
    print(f"[cyan]Audio for timing:[/cyan] {audio_path}")

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
