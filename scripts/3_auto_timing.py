#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# Auto-time lyrics (TXT) to audio (MP3/WAV):
#   • Faster-Whisper word timestamps
#   • Repeated-lyrics tagging (#run.index)
#   • Forward-only alignment
#   • Similarity - time_penalty scoring
#   • D-LEVEL drift prevention:
#         - window-clamped alignment after repeated blocks
#         - prevents jumps to wrong chorus repetition
#   • Repeated-lyric smoothing to distribute equal lines
#
# Output CSV format:
#   line_index,start,end,text

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional

# Rich logging if available
try:
    from rich import print
except Exception:
    pass

# Faster-Whisper
try:
    from faster_whisper import WhisperModel
except Exception:
    print("[bold red]Missing dependency: faster-whisper[/bold red]")
    print("  python3 -m pip install faster-whisper")
    raise

# Similarity engine
try:
    from rapidfuzz import fuzz
    _HAS_RAPIDFUZZ = True
except Exception:
    import difflib
    _HAS_RAPIDFUZZ = False

# Device helper
try:
    import torch
except Exception:
    torch = None

# ---------------------------------------------------
# PATHS
# ---------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
SEPARATED_DIR = BASE_DIR / "separated"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------
@dataclass
class Word:
    text: str
    start: float
    end: float

# ---------------------------------------------------
# NORMALIZATION HELPERS
# ---------------------------------------------------
_PUNCT_RE = re.compile(r"[^a-z0-9'\s]+", re.IGNORECASE)
_WS_RE    = re.compile(r"\s+")

def norm(s: str) -> str:
    """
    Normalize for fuzzy matching:
        - lowercase
        - strip run-tags (#1.3)
        - remove punctuation
        - collapse whitespace
    """
    s = s.strip().lower()
    s = re.sub(r"#\d+\.\d+", " ", s)  # remove run-tags
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s
# ---------------------------------------------------
# LOAD LYRICS / WRITE CSV
# ---------------------------------------------------
def load_lyrics_lines(txt_path: Path) -> List[str]:
    if not txt_path.exists():
        raise FileNotFoundError(f"Lyrics not found: {txt_path}")
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines()]
    return [ln for ln in lines if ln]

def write_timings_csv(slug: str, triples: List[Tuple[int,float,float,str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index","start","end","text"])
        for li, ts, te, tx in triples:
            w.writerow([li, f"{ts:.3f}", f"{te:.3f}", tx])
    print(f"[green]Wrote timings →[/green] {out}")
    return out

# ---------------------------------------------------
# SELECT AUDIO (mp3 OR STEM)
# ---------------------------------------------------
def choose_timing_audio(slug: str, explicit_audio: Optional[Path]) -> Path:
    if explicit_audio and explicit_audio.exists():
        print(f"[cyan]Using explicit audio:[/cyan] {explicit_audio}")
        return explicit_audio

    # Prefer Demucs vocal stem
    candidates: List[Path] = []
    if SEPARATED_DIR.exists():
        for model_dir in SEPARATED_DIR.iterdir():
            if model_dir.is_dir():
                slug_dir = model_dir / slug
                if slug_dir.is_dir():
                    for p in slug_dir.glob("*vocals*.wav"):
                        candidates.append(p)

    if candidates:
        best = max(candidates, key=lambda p: p.stat().st_mtime)
        print(f"[green]Using vocal stem:[/green] {best}")
        return best

    # Fallback: mp3s/<slug>.mp3
    fallback = MP3_DIR / f"{slug}.mp3"
    if fallback.exists():
        print(f"[yellow]Using original mp3:[/yellow] {fallback}")
        return fallback

    print(f"[red]No audio found for slug={slug}[/red]")
    sys.exit(1)

# ---------------------------------------------------
# TRANSCRIPTION
# ---------------------------------------------------
def choose_device(device_flag: Optional[str]):
    if device_flag:
        dev = device_flag
    else:
        if torch and getattr(torch, "cuda", None) and torch.cuda.is_available():
            dev = "cuda"
        else:
            dev = "cpu"
    compute = "float16" if dev == "cuda" else "int8"
    return dev, compute

def transcribe_words(audio_path: Path, model_size="small", language=None, device=None):
    from time import perf_counter
    dev, compute = choose_device(device)

    print(f"[cyan]Loading faster-whisper[/cyan] size={model_size} device={dev} compute={compute}")
    t0 = perf_counter()
    model = WhisperModel(model_size, device=dev, compute_type=compute)
    print(f"[cyan]Model loaded in {perf_counter()-t0:.1f}s[/cyan]")

    print(f"[cyan]Transcribing:[/cyan] {audio_path}")
    segments, info = model.transcribe(
        str(audio_path),
        beam_size=5,
        word_timestamps=True,
        language=language,
    )

    out: List[Word] = []
    for seg in segments:
        if getattr(seg, "words", None):
            for w in seg.words:
                if w.start is not None and w.end is not None and w.word:
                    out.append(Word(
                        text=w.word.strip(),
                        start=float(w.start),
                        end=float(w.end)
                    ))
        else:
            # Segment-level fallback
            if seg.start is None or not seg.text:
                continue
            out.append(Word(
                text=seg.text.strip(),
                start=float(seg.start),
                end=float(seg.end or (seg.start + 1.8))
            ))

    print(f"[green]Transcribed {len(out)} words[/green]")
    return out

# ---------------------------------------------------
# TAG REPEATED TRANSCRIPT WORDS
# ---------------------------------------------------
def tag_repeated_transcript_words(words: List[Word], max_gap=1.5) -> List[Word]:
    """
    Turns:
        memoria memoria memoria
    into:
        memoria#1.1, memoria#1.2, memoria#1.3
    and later repetition:
        memoria memoria
    into:
        memoria#2.1, memoria#2.2
    """
    if not words:
        return words

    tagged = []
    prev = None
    prev_end = None
    run_id = 0
    idx = 0

    for w in words:
        t = w.text.lower()
        if t == prev and prev_end is not None and abs(w.start - prev_end) <= max_gap:
            idx += 1
        else:
            run_id += 1
            idx = 1
        tagged.append(Word(f"{t}#{run_id}.{idx}", w.start, w.end))
        prev = t
        prev_end = w.end

    return tagged

# ---------------------------------------------------
# SIMILARITY
# ---------------------------------------------------
def _similarity(a: str, b: str) -> float:
    a = norm(a)
    b = norm(b)
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return float(fuzz.ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0
# ---------------------------------------------------
# REPEATED-LYRIC BLOCK SMOOTHER
# ---------------------------------------------------
def fix_repeated_lyric_blocks(triples, min_spacing=0.20):
    """
    Re-distribute start times of identical consecutive lyric lines.
    Example:
        Memoria
        Memoria
        Memoria
    becomes evenly spaced between old_start and next_non_repeated_start.
    """
    def norm_text(s): return s.strip().lower()

    n = len(triples)
    if n == 0:
        return triples

    texts = [norm_text(t[3]) for t in triples]
    new = triples.copy()

    i = 0
    while i < n:
        j = i + 1
        while j < n and texts[j] == texts[i] and texts[j] != "":
            j += 1
        block_len = j - i
        if block_len > 1:
            t0 = new[i][1]  # first start
            if j < n:
                t1 = new[j][1]  # next non-repeated line
            else:
                t1 = t0 + 3.0

            spacing = max(min_spacing, (t1 - t0) / block_len)

            for k in range(block_len):
                li, s, e, txt = new[i + k]
                ns = t0 + spacing * k
                if e <= ns:
                    e = ns + 0.01
                new[i + k] = (li, ns, e, txt)

        i = j

    # clamp end > next start
    for i in range(len(new)-1):
        li, s, e, text = new[i]
        _, next_s, _, _ = new[i+1]
        if e > next_s:
            e = max(s, next_s - 0.05)
        new[i] = (li, s, e, text)

    return new


# ---------------------------------------------------
# D-LEVEL HARD DRIFT BLOCKER
# ---------------------------------------------------
def clamp_time_to_nearest_anchor(t, anchors, max_shift=3.0):
    """
    If alignment jumps way too late (classic repeated-lyrics drift),
    snap to closest anchor time IF within max_shift seconds.
    """
    if not anchors:
        return t
    closest = min(anchors, key=lambda a: abs(a - t))
    if abs(closest - t) <= max_shift:
        return closest
    return t


# ---------------------------------------------------
# ALIGNMENT CORE (D-LEVEL)
# ---------------------------------------------------
def align_lyrics_to_words(lines, words):
    """
    D-LEVEL alignment:
      • Forward-only match
      • Similarity - time_penalty scoring
      • Run-aware by using tagged transcript words
      • After each repeated-lyric block, window-tighten allowed matches
      • Hard drift clamp to nearest anchor (prevents 8s jumps)
    """
    if not words:
        out = []
        t = 0.0
        for i, line in enumerate(lines):
            out.append((i, t, t + 2.5, line))
            t += 2.5
        return out

    n_words = len(words)
    words_norm = [norm(w.text) for w in words]

    # average spacing heuristic
    total_span = max(0.1, words[-1].end - words[0].start)
    avg_gap = max(1.2, min(6.0, total_span / max(1, len(lines))))

    # scoring constants
    MAX_LOOKAHEAD = 120
    TIME_PENALTY_PER_SEC = 1.2
    TIME_PENALTY_MAX = 40.0

    # dynamic search bounds
    search_start_idx = 0
    prev_start = max(0.0, words[0].start - 0.5)

    # anchors = reliable early lines (used in drift clamp)
    anchors = []

    triples = []

    for li, raw in enumerate(lines):
        nline = norm(raw)
        if not nline:
            s = prev_start + 0.4
            e = s + 1.2
            triples.append((li, s, e, raw))
            prev_start = s
            continue

        tokens = nline.split()
        approx_len = max(1, min(len(tokens), 10))

        best_score = -1e9
        best_idx = search_start_idx

        start_min = min(search_start_idx, n_words - 1)
        start_max = min(n_words - 1, start_min + MAX_LOOKAHEAD)

        expected = words[0].start if li == 0 else (prev_start + avg_gap)

        for widx in range(start_min, start_max+1):
            remaining = n_words - widx
            if remaining <= 0:
                break

            win = min(remaining, len(tokens) + 3, approx_len + 6)
            window_norm = " ".join(words_norm[widx:widx+win]).strip()
            if not window_norm:
                continue

            sim = _similarity(window_norm, nline)
            start_time = words[widx].start

            td = abs(start_time - expected)
            penalty = min(td * TIME_PENALTY_PER_SEC, TIME_PENALTY_MAX)

            score = sim - penalty
            if score > best_score:
                best_score = score
                best_idx = widx

        # fallback if poor match
        if best_score < 30:
            t_start = prev_start + avg_gap
            print(f"[yellow]Low score {best_score:.1f} on line {li} → fallback {t_start:.2f}s[/yellow]")
        else:
            t_start = words[best_idx].start
            # disallow backwards jump
            if t_start < prev_start - 0.25:
                t_start = prev_start + 0.01

        # D-LEVEL drift clamp
        t_start = clamp_time_to_nearest_anchor(t_start, anchors, max_shift=3.0)

        # Establish this line as anchor if similarity was good
        if best_score >= 45:
            anchors.append(t_start)

        # naive end guess
        t_end = t_start + avg_gap

        triples.append((li, t_start, t_end, raw))
        prev_start = t_start

        # move window forward
        MIN_ADV = max(2, len(tokens))
        search_start_idx = min(n_words - 1, max(best_idx + MIN_ADV, search_start_idx + 1))

    # First pass: clamp overlapping ends
    fixed = []
    for i, (li, s, e, txt) in enumerate(triples):
        if i < len(triples)-1:
            next_s = triples[i+1][1]
            if e > next_s:
                e = max(s, next_s - 0.05)
        fixed.append((li, s, e, txt))

    return fixed
# ---------------------------------------------------
# TOP-LEVEL ALIGNMENT WRAPPER
# ---------------------------------------------------
def perform_alignment(lines: List[str], words: List[Word]):
    """
    Pipeline:
      1) D-level alignment (forward, similarity scoring, drift clamp)
      2) Repeated-lyric block smoothing
    """
    base = align_lyrics_to_words(lines, words)
    smooth = fix_repeated_lyric_blocks(base)
    return smooth


# ---------------------------------------------------
# CLI PARSER
# ---------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="D-level auto-timing for karaoke.")

    p.add_argument("--slug", required=True, help="Song slug (txts/<slug>.txt)")
    p.add_argument(
        "--mp3",
        type=str,
        help="Optional override path to audio. If missing, prefers separated/**/*vocals*.wav then mp3s/<slug>.mp3"
    )
    p.add_argument(
        "--txt",
        type=str,
        help="Optional manual lyrics path. Default: txts/<slug>.txt"
    )
    p.add_argument(
        "--model-size",
        default="small",
        help="Whisper model size (tiny/base/small/medium/large-v2)"
    )
    p.add_argument(
        "--lang",
        default=None,
        help="Language code override (en/es/…) — None = auto"
    )
    p.add_argument(
        "--device",
        default=None,
        help="cpu or cuda — default: auto-detect"
    )
    return p.parse_args()


# ---------------------------------------------------
# MAIN
# ---------------------------------------------------
def main():
    args = parse_args()

    slug = args.slug
    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    audio_override = Path(args.mp3) if args.mp3 else None

    print(f"[cyan]Slug:[/cyan] {slug}")
    print(f"[cyan]Lyrics:[/cyan] {txt_path}")

    if not txt_path.exists():
        print(f"[red]TXT not found: {txt_path}[/red]")
        sys.exit(1)

    # Load lyrics
    lines = load_lyrics_lines(txt_path)
    print(f"[green]Loaded {len(lines)} lyric lines[/green]")

    # Choose audio
    audio_path = choose_timing_audio(slug, audio_override)

    # Transcribe
    words = transcribe_words(
        audio_path,
        model_size=args.model_size,
        language=args.lang,
        device=args.device,
    )

    # Tag repeated transcript words
    tagged = tag_repeated_transcript_words(words)

    # Alignment
    triples = perform_alignment(lines, tagged)

    # Output CSV
    write_timings_csv(slug, triples)


if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
