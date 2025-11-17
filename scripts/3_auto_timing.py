#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# Auto-time lyrics (TXT) to audio using Gentle (HTTP forced aligner):
# - Sends audio + full lyrics text to Gentle over HTTP
# - Receives per-word timestamps from Gentle
# - Globally aligns lyric lines to aligned words (DP over tokens)
# - Derives each line's start/end from matched word indices
# - Fills gaps by interpolation; enforces monotonically increasing timings
# - Emits timings/<slug>.csv with header: line_index,start,end,text
#
# Requirements:
#   - Gentle running in HTTP mode (Option 1):
#       ./gentle --http --port 8765
#   - Python deps:
#       python3 -m pip install requests rapidfuzz
#
# Usage:
#   python3 scripts/3_auto_timing.py --slug nirvana_come_as_you_are
#
#   python3 scripts/3_auto_timing.py \
#       --slug nirvana_come_as_you_are \
#       --mp3 mp3s/nirvana_come_as_you_are.mp3 \
#       --txt txts/nirvana_come_as_you_are.txt \
#       --gentle-url http://localhost:8765/transcriptions?async=false

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Optional pretty logging via rich (falls back to normal print)
try:
    from rich import print  # type: ignore
except Exception:  # pragma: no cover
    pass

try:
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    print("[bold red]Missing dependency:[/bold red] requests")
    print("  python3 -m pip install requests")
    raise

try:
    from rapidfuzz import fuzz  # type: ignore
    _HAS_RAPIDFUZZ = True
except Exception:
    import difflib  # type: ignore
    _HAS_RAPIDFUZZ = False

# ---------- Paths ----------
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

MIXES_DIR = BASE_DIR / "mixes"
INTERMEDIATE_DIR = BASE_DIR / "intermediate"
GENTLE_AUDIO_DIR = INTERMEDIATE_DIR / "gentle_audio"
GENTLE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

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
    Normalize text for matching / tokenizing:
    - lowercase
    - keep apostrophes
    - strip punctuation
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


def load_lyrics_text(txt_path: Path) -> str:
    """
    Load full lyrics text as a single string to send to Gentle.
    """
    if not txt_path.exists():
        raise FileNotFoundError(f"TXT not found: {txt_path}")
    return txt_path.read_text(encoding="utf-8", errors="ignore")


def write_timings_csv(slug: str, triples: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, ts, te, tx in triples:
            w.writerow([li, f"{ts:.3f}", f"{te:.3f}", tx])
    print(f"[green]Wrote timings:[/green] {out} ({len(triples)} rows)")
    return out


# ---------- Audio selection & conversion for Gentle ----------
def choose_timing_audio(slug: str, explicit_audio: Optional[Path]) -> Path:
    """
    Select the best audio source for alignment.

    Priority:
      1) Explicit --mp3/--audio path (any extension)
      2) mixes/<slug>_*.wav (most recent)
      3) mp3s/<slug>.mp3

    This path is then converted (if needed) to a Gentle-friendly WAV.
    """
    # 1) CLI override wins
    if explicit_audio is not None and explicit_audio.exists():
        print(f"[cyan]Using explicit audio for alignment:[/cyan] {explicit_audio}")
        return explicit_audio

    # 2) Prefer a WAV full-mix from mixes/<slug>_<profile>.wav
    mix_candidates = list(MIXES_DIR.glob(f"{slug}_*.wav"))
    if mix_candidates:
        best = max(mix_candidates, key=lambda p: p.stat().st_mtime)
        print(f"[green]Using mixed full audio for alignment:[/green] {best}")
        return best

    # 3) Fallback to mp3s/<slug>.mp3
    mp3_path = MP3_DIR / f"{slug}.mp3"
    if mp3_path.exists():
        print(f"[yellow]Using original mp3 for alignment:[/yellow] {mp3_path}")
        return mp3_path

    print(f"[bold red]No audio found for alignment for slug={slug}[/bold red]")
    sys.exit(1)


def convert_to_gentle_wav(src: Path) -> Path:
    """
    Convert source audio to a Gentle-friendly WAV (mono, 16kHz).
    If src is already a .wav, we still convert into our dedicated dir
    to enforce consistent format.
    """
    if not src.exists():
        raise FileNotFoundError(f"Audio not found: {src}")

    out = GENTLE_AUDIO_DIR / f"{src.stem}_gentle.wav"
    if out.exists():
        print(f"[cyan]Reusing existing Gentle WAV:[/cyan] {out}")
        return out

    print(
        f"[cyan]Converting audio for Gentle alignment[/cyan]\n"
        f"  In:  {src}\n"
        f"  Out: {out}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",        # mono
        "-ar",
        "16000",    # 16 kHz
        str(out),
    ]
    try:
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[bold red]ffmpeg conversion failed:[/bold red] {e}")
        sys.exit(1)

    if not out.exists():
        print("[bold red]Gentle WAV not created; aborting.[/bold red]")
        sys.exit(1)

    return out


def probe_audio_duration(path: Path) -> float:
    """
    Use ffprobe to get audio duration in seconds.
    """
    if not path.exists():
        return 0.0
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return float(out.strip())
    except Exception:
        return 0.0


# ---------- Gentle HTTP client ----------
def call_gentle(
    gentle_url: str,
    audio_wav: Path,
    transcript_text: str,
) -> Dict:
    """
    Call Gentle HTTP server with audio + transcript text.

    gentle_url example:
      http://localhost:8765/transcriptions?async=false

    Returns parsed JSON dict.
    """
    if not audio_wav.exists():
        raise FileNotFoundError(f"Gentle audio not found: {audio_wav}")

    print(f"[cyan]Contacting Gentle at:[/cyan] {gentle_url}")

    files = {
        "audio": ("audio.wav", audio_wav.open("rb"), "audio/wav"),
    }
    data = {
        "transcript": transcript_text,
    }

    try:
        resp = requests.post(gentle_url, files=files, data=data, timeout=600)
    except Exception as e:
        print(
            "[bold red]Failed to reach Gentle HTTP server.[/bold red]\n"
            f"URL: {gentle_url}\n"
            f"Error: {e}"
        )
        sys.exit(1)

    if resp.status_code != 200:
        print(
            "[bold red]Gentle returned non-200 status.[/bold red]\n"
            f"Status: {resp.status_code}\n"
            f"Body: {resp.text[:1000]}"
        )
        sys.exit(1)

    try:
        payload = resp.json()
    except json.JSONDecodeError:
        print("[bold red]Failed to parse JSON from Gentle response.[/bold red]")
        print(f"Raw response (truncated): {resp.text[:1000]}")
        sys.exit(1)

    return payload


def words_from_gentle(payload: Dict) -> List[Word]:
    """
    Extract a flat list of Word objects from Gentle JSON payload.

    Expected structure (simplified):

    {
      "transcript": "...",
      "words": [
        {
          "case": "success",
          "alignedWord": "hello",
          "start": 12.34,
          "end": 12.78,
          ...
        },
        {
          "case": "not-found-in-audio",
          ...
        }
      ]
    }
    """
    words_json = payload.get("words", [])
    words: List[Word] = []

    for w in words_json:
        case = w.get("case", "")
        if case != "success":
            # Skip tokens that Gentle couldn't align to audio
            continue
        start = w.get("start", None)
        end = w.get("end", None)
        if start is None or end is None:
            continue

        # Prefer alignedWord; fall back to "word" or raw transcript word
        text = (
            w.get("alignedWord")
            or w.get("word")
            or ""
        )
        text = (text or "").strip()
        if not text:
            continue

        words.append(Word(text=text, start=float(start), end=float(end)))

    if words:
        print(
            f"[green]Gentle aligned {len(words)} words[/green] "
            f"(first at {words[0].start:.2f}s, last at {words[-1].end:.2f}s)"
        )
    else:
        print("[bold red]No aligned words from Gentle.[/bold red]")

    return words


# ---------- Similarity ----------
def _similarity(a: str, b: str) -> float:
    """
    Return a similarity score in roughly [0, 100].
    """
    a = norm(a)
    b = norm(b)
    if not a or not b:
        return 0.0
    if _HAS_RAPIDFUZZ:
        return float(fuzz.ratio(a, b))
    # Fallback to difflib
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0  # type: ignore[name-defined]


def _tokens_similar(a: str, b: str, threshold: float = 70.0) -> bool:
    """
    Decide if two tokens should be considered a "match" in the DP alignment.
    """
    return _similarity(a, b) >= threshold


# ---------- DP-based global alignment (lyrics lines -> Gentle words) ----------
def align_lines_dp(
    lines: List[str],
    words: List[Word],
    audio_duration: float,
) -> List[Tuple[int, float, float, str]]:
    """
    Global sequence alignment between:
      - lyric tokens (with line indices)
      - Gentle tokens (one per aligned Word)

    Steps:
      1) Flatten lyrics into tokens with line indices.
      2) Flatten Gentle-aligned words into normalized tokens.
      3) Dynamic-programming alignment (Levenshtein-style).
      4) For each line, derive [start,end] from earliest/latest matched word index.
      5) Interpolate any lines that didn't get matches.
      6) Enforce monotone, non-overlapping timings, clamped to audio_duration.
    """
    n_lines = len(lines)
    if n_lines == 0:
        return []

    # ---- Build lyric token sequence ----
    lyric_tokens: List[Tuple[str, int]] = []  # (token_text, line_index)
    for li, line in enumerate(lines):
        toks = norm(line).split()
        for t in toks:
            if t:
                lyric_tokens.append((t, li))

    # ---- Build Gentle token sequence ----
    transcript_tokens: List[str] = []
    tok2word_index: List[int] = []  # map transcript-token index -> Word index

    for wi, w in enumerate(words):
        tok = norm(w.text)
        if not tok:
            continue
        transcript_tokens.append(tok)
        tok2word_index.append(wi)

    if not lyric_tokens or not transcript_tokens:
        # Fallback: naive spacing across the audio duration
        print(
            "[yellow]Token sequences empty; falling back to naive linear spacing.[/yellow]"
        )
        total_span = audio_duration if audio_duration > 0 else 2.5 * n_lines
        avg_gap = total_span / max(1, n_lines)
        triples: List[Tuple[int, float, float, str]] = []
        t = 0.0
        for li, line in enumerate(lines):
            start = t
            end = t + avg_gap
            triples.append((li, start, end, line))
            t = end
        return triples

    L = len(lyric_tokens)
    T = len(transcript_tokens)

    print(
        f"[cyan]DP alignment (lyrics vs Gentle words):[/cyan] "
        f"{L} lyric tokens vs {T} aligned tokens ({n_lines} lines)"
    )

    # ---- DP matrix ----
    INF = 10**9
    # dp[i][j] = best cost to align first i lyric tokens with first j Gentle tokens
    dp: List[List[int]] = [[INF] * (T + 1) for _ in range(L + 1)]
    prev: List[List[Optional[Tuple[str, int, int]]]] = [
        [None] * (T + 1) for _ in range(L + 1)
    ]

    dp[0][0] = 0

    for i in range(L + 1):
        for j in range(T + 1):
            cur = dp[i][j]
            if cur == INF:
                continue

            # Match / substitute both sequences (advance i and j)
            if i < L and j < T:
                tok_l, _li = lyric_tokens[i]
                tok_t = transcript_tokens[j]
                cost = 0 if _tokens_similar(tok_l, tok_t) else 1
                if cur + cost < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cur + cost
                    prev[i + 1][j + 1] = ("M", i, j)

            # Skip Gentle token (insertion in lyrics)
            if j < T:
                if cur + 1 < dp[i][j + 1]:
                    dp[i][j + 1] = cur + 1
                    prev[i][j + 1] = ("T", i, j)

            # Skip lyric token (deletion)
            if i < L:
                if cur + 1 < dp[i + 1][j]:
                    dp[i + 1][j] = cur + 1
                    prev[i + 1][j] = ("L", i, j)

    # We align full sequences → end at (L, T)
    if dp[L][T] >= INF:
        print(
            "[yellow]DP failed to find a finite alignment; falling back to naive spacing.[/yellow]"
        )
        total_span = audio_duration if audio_duration > 0 else 2.5 * n_lines
        avg_gap = total_span / max(1, n_lines)
        triples: List[Tuple[int, float, float, str]] = []
        t = 0.0
        for li, line in enumerate(lines):
            start = t
            end = t + avg_gap
            triples.append((li, start, end, line))
            t = end
        return triples

    print(f"[green]DP alignment cost:[/green] {dp[L][T]}")

    # ---- Backtrack to collect matches ----
    line_to_word_indices: Dict[int, List[int]] = {}

    i, j = L, T
    while i > 0 or j > 0:
        step = prev[i][j]
        if step is None:
            break
        op, pi, pj = step
        if op == "M":
            tok_l, line_idx = lyric_tokens[pi]
            # Only treat as a match if tokens are reasonably similar
            tok_t = transcript_tokens[pj]
            if _tokens_similar(tok_l, tok_t):
                word_idx = tok2word_index[pj]
                line_to_word_indices.setdefault(line_idx, []).append(word_idx)
        # Move to predecessor
        i, j = pi, pj

    # ---- Derive per-line start/end from word indices ----
    # First, compute avg_gap from Gentle span
    if words:
        total_span = max(0.1, words[-1].end - words[0].start)
    else:
        total_span = audio_duration if audio_duration > 0 else 2.5 * n_lines
    avg_gap = max(1.0, min(6.0, total_span / max(1, n_lines)))

    per_line: List[Tuple[int, Optional[float], Optional[float], str]] = []
    for li in range(n_lines):
        line = lines[li]
        idxs = line_to_word_indices.get(li)
        if idxs:
            w_min = min(idxs)
            w_max = max(idxs)
            start = float(words[w_min].start)
            end = float(words[w_max].end)
            if end <= start:
                end = start + 0.25
            per_line.append((li, start, end, line))
        else:
            # Placeholder; filled later
            per_line.append((li, None, None, line))

    # ---- Interpolate lines that didn't get direct matches ----
    def fill_missing(
        arr: List[Tuple[int, Optional[float], Optional[float], str]]
    ) -> List[Tuple[int, float, float, str]]:
        out: List[Tuple[int, float, float, str]] = list(arr)  # shallow copy

        # Pass 1: we leave None as-is; main work is in block interpolation
        i = 0
        while i < len(out):
            li, s, e, text = out[i]
            if s is not None and e is not None:
                i += 1
                continue

            # start of a missing block
            start_block = i
            while i < len(out):
                li2, s2, e2, _txt2 = out[i]
                if s2 is not None and e2 is not None:
                    break
                i += 1
            end_block = i - 1  # inclusive

            # neighbors
            left = start_block - 1
            right = i if i < len(out) else None

            if left >= 0 and right is not None and right < len(out):
                # Interpolate between left and right
                _, sL, eL, _ = out[left]
                _, sR, eR, _ = out[right]
                assert sL is not None and eL is not None and sR is not None and eR is not None
                span = max(0.5, sR - eL)
                n_missing = end_block - start_block + 1
                step = span / (n_missing + 1)
                cur_start = eL + step
                for k in range(start_block, end_block + 1):
                    li_k, _s_k, _e_k, text_k = out[k]
                    s_k = cur_start
                    e_k = s_k + avg_gap
                    out[k] = (li_k, s_k, e_k, text_k)
                    cur_start += step
            elif left >= 0:
                # Only left neighbor; continue forward with avg_gap
                _, sL, eL, _ = out[left]
                assert sL is not None and eL is not None
                cur_start = eL + 0.25
                for k in range(start_block, end_block + 1):
                    li_k, _s_k, _e_k, text_k = out[k]
                    s_k = cur_start
                    e_k = s_k + avg_gap
                    out[k] = (li_k, s_k, e_k, text_k)
                    cur_start += avg_gap
            elif right is not None and right < len(out):
                # Only right neighbor; walk backward from it
                _, sR, eR, _ = out[right]
                assert sR is not None and eR is not None
                cur_end = sR - 0.25
                for k in range(end_block, start_block - 1, -1):
                    li_k, _s_k, _e_k, text_k = out[k]
                    e_k = cur_end
                    s_k = e_k - avg_gap
                    if s_k < 0:
                        s_k = 0.0
                    out[k] = (li_k, s_k, e_k, text_k)
                    cur_end = s_k - 0.25
            else:
                # No neighbors at all → space from 0
                cur_start = 0.0
                for k in range(start_block, end_block + 1):
                    li_k, _s_k, _e_k, text_k = out[k]
                    s_k = cur_start
                    e_k = s_k + avg_gap
                    out[k] = (li_k, s_k, e_k, text_k)
                    cur_start += avg_gap

        # Replace any remaining None (paranoia)
        for i in range(len(out)):
            li, s, e, text = out[i]
            if s is None or e is None:
                s = 0.0 if i == 0 else (out[i - 1][1] or 0.0) + avg_gap
                e = s + avg_gap
                out[i] = (li, s, e, text)

        # Cast types
        return [(li, float(s), float(e), text) for (li, s, e, text) in out]

    filled = fill_missing(per_line)

    # ---- Enforce monotone, non-overlapping timings ----
    filled.sort(key=lambda t: t[0])  # sort by line_index, just in case

    MIN_GAP = 0.05
    for idx in range(1, len(filled)):
        li, s, e, text = filled[idx]
        _li_prev, s_prev, e_prev, _txt_prev = filled[idx - 1]
        if s < s_prev + MIN_GAP:
            s = s_prev + MIN_GAP
        if e <= s:
            e = s + 0.01
        filled[idx] = (li, s, e, text)

    # Clamp ends so that end[i] <= start[i+1] - MIN_GAP
    for idx in range(len(filled) - 1):
        li, s, e, text = filled[idx]
        _li_n, s_next, _e_n, _txt_n = filled[idx + 1]
        max_e = s_next - MIN_GAP
        if e > max_e:
            e = max(s + 0.01, max_e)
        filled[idx] = (li, s, e, text)

    # Clamp to audio_duration
    if audio_duration > 0:
        clamped: List[Tuple[int, float, float, str]] = []
        for li, s, e, text in filled:
            if s >= audio_duration:
                # Drop lines entirely beyond the audio
                continue
            e = min(e, audio_duration)
            if e <= s:
                e = min(audio_duration, s + 0.01)
            clamped.append((li, s, e, text))
        filled = clamped

    return filled


# ---------- Wrapper ----------
def perform_alignment(
    lines: List[str],
    words: List[Word],
    audio_duration: float,
) -> List[Tuple[int, float, float, str]]:
    """
    Top-level alignment wrapper.
    """
    if not words:
        print(
            "[yellow]No words from Gentle; using naive linear spacing across track.[/yellow]"
        )
        total_span = audio_duration if audio_duration > 0 else 2.5 * len(lines)
        avg_gap = total_span / max(1, len(lines))
        triples: List[Tuple[int, float, float, str]] = []
        t = 0.0
        for idx, line in enumerate(lines):
            triples.append((idx, t, t + avg_gap, line))
            t += avg_gap
        return triples

    return align_lines_dp(lines, words, audio_duration)


# ---------- CLI ----------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-time lyrics TXT to audio using Gentle (HTTP forced aligner)."
    )

    parser.add_argument(
        "--slug",
        required=True,
        help="Song slug (used for txts/<slug>.txt, mp3s/<slug>.mp3, timings/<slug>.csv).",
    )
    parser.add_argument(
        "--mp3",
        type=str,
        help=(
            "Path to audio file (any format ffmpeg can read). If omitted, "
            "will prefer mixes/<slug>_*.wav, falling back to mp3s/<slug>.mp3."
        ),
    )
    parser.add_argument(
        "--txt",
        type=str,
        help="Path to lyrics TXT. Default: txts/<slug>.txt",
    )
    parser.add_argument(
        "--gentle-url",
        type=str,
        default="http://localhost:8765/transcriptions?async=false",
        help="Gentle HTTP endpoint (default: http://localhost:8765/transcriptions?async=false).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    slug = args.slug
    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    explicit_audio = Path(args.mp3) if args.mp3 else None

    base_audio = choose_timing_audio(slug, explicit_audio)
    gentle_wav = convert_to_gentle_wav(base_audio)

    print(f"[cyan]Slug:[/cyan]  {slug}")
    print(f"[cyan]TXT:[/cyan]   {txt_path}")
    print(f"[cyan]Audio (base):[/cyan] {base_audio}")
    print(f"[cyan]Audio for Gentle:[/cyan] {gentle_wav}")

    if not txt_path.exists():
        print(f"[bold red]TXT not found:[/bold red] {txt_path}")
        sys.exit(1)

    audio_duration = probe_audio_duration(base_audio)
    if audio_duration > 0:
        print(f"[cyan]Audio duration:[/cyan] {audio_duration:.2f}s")
    else:
        print("[yellow]Could not determine audio duration via ffprobe.[/yellow]")

    # 1) Load lyrics
    lines = load_lyrics_lines(txt_path)
    transcript_text = load_lyrics_text(txt_path)
    print(f"[green]Loaded {len(lines)} lyric lines[/green]")

    # 2) Call Gentle
    payload = call_gentle(
        gentle_url=args.gentle_url,
        audio_wav=gentle_wav,
        transcript_text=transcript_text,
    )

    # 3) Extract words
    words = words_from_gentle(payload)

    # 4) Alignment (DP-based)
    triples = perform_alignment(lines, words, audio_duration)

    # 5) Write CSV
    write_timings_csv(slug, triples)


if __name__ == "__main__":
    main()
# end of 3_auto_timing.py
