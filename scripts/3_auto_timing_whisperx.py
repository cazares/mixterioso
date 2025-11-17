#!/usr/bin/env python3
# scripts/3_auto_timing_whisperx.py
#
# Auto-time lyrics using WhisperX (ASR + wav2vec2 alignment).
# - CPU-safe (int8) — works on macOS without GPU
# - Uses wav2vec2 premium aligner (no CTC shortcuts)
# - Does NOT pass deprecated/invalid kwargs like `vad` or `compute_type` to align model
# - Emits timings/<slug>.csv with header: line_index,start,end,text

from __future__ import annotations

import argparse
import csv
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ----- Color logging (rich) -----
try:
    from rich import print  # type: ignore
except Exception:  # pragma: no cover
    # Fallback to builtin print if rich is not available
    pass

# ----- Paths -----
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# ----- External deps -----
import torch  # type: ignore
import whisperx  # type: ignore

# ----- Normalization helpers -----
_PUNCT_RE = re.compile(r"[^a-z0-9'\s]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def norm(s: str) -> str:
    """Normalize tokens for matching."""
    s = s.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


# ----- I/O helpers -----
def load_lyrics(txt_path: Path) -> List[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines()]
    return [ln for ln in lines if ln]


def choose_audio(slug: str, explicit: Optional[Path]) -> Path:
    """Prefer explicit path, then mixes/<slug>_*.wav, then mp3s/<slug>.mp3."""
    if explicit and explicit.exists():
        print(f"[cyan]Using explicit audio:[/cyan] [bold]{explicit}[/bold]")
        return explicit

    mix_candidates = list(MIXES_DIR.glob(f"{slug}_*.wav"))
    if mix_candidates:
        best = max(mix_candidates, key=lambda p: p.stat().st_mtime)
        print(f"[green]Using mix audio:[/green] [bold]{best}[/bold]")
        return best

    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        print(f"[yellow]Using mp3 audio:[/yellow] [bold]{mp3}[/bold]")
        return mp3

    print(f"[bold red]No audio found for slug={slug}[/bold red]")
    sys.exit(1)


def write_csv(slug: str, rows: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, s, e, text in rows:
            w.writerow([li, f"{s:.3f}", f"{e:.3f}", text])
    print(f"[green]Wrote timings CSV:[/green] [bold]{out}[/bold] ([cyan]{len(rows)}[/cyan] lines)")
    return out


# ----- WhisperX ASR + alignment -----
def run_asr(audio_path: Path, language: str, device: str) -> dict:
    """Run WhisperX ASR with CPU-safe settings."""
    print(
        f"[magenta]Loading WhisperX ASR model[/magenta] "
        f"(arch=[bold]small.en[/bold], device=[bold]{device}[/bold], compute_type=[bold]int8[/bold])"
    )
    model = whisperx.load_model(
        "small.en" if language == "en" else "small",
        device=device,
        compute_type="int8",
    )

    print(f"[cyan]Running ASR on:[/cyan] [bold]{audio_path}[/bold]")
    result = model.transcribe(str(audio_path), batch_size=8)
    segs = result.get("segments", [])
    print(f"[green]ASR complete:[/green] [bold]{len(segs)}[/bold] segments")
    return result


def run_alignment(asr_result: dict, audio_path: Path, language: str, device: str) -> dict:
    """
    Run wav2vec2-based forced alignment via WhisperX.

    NOTE: API is:
      align_model, metadata = whisperx.load_align_model(language_code=..., device=...)
      aligned = whisperx.align(segments, align_model, metadata, audio_path, device=...)
    No compute_type / vad kwargs here.
    """
    print(
        f"[magenta]Loading wav2vec2 aligner[/magenta] "
        f"(lang=[bold]{language}[/bold], device=[bold]{device}[/bold])"
    )
    align_model, metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
    )

    print(f"[cyan]Running forced alignment on:[/cyan] [bold]{audio_path}[/bold]")
    aligned = whisperx.align(
        asr_result["segments"],
        align_model,
        metadata,
        str(audio_path),
        device=device,
    )

    words = aligned.get("word_segments", [])
    print(f"[green]Alignment complete:[/green] [bold]{len(words)}[/bold] word segments")
    return aligned


# ----- DP-based lyric line alignment -----
def align_lines_dp(lines: List[str], words: List[dict]) -> List[Tuple[int, float, float, str]]:
    """
    Map aligned word-level timings to line-level [start, end] using DP to
    softly align tokens.

    words: list of dicts with keys: "word", "start", "end".
    """
    n_lines = len(lines)
    if n_lines == 0:
        return []

    # Tokenize lyrics with line indices
    lyric_tokens: List[Tuple[str, int]] = []
    for li, line in enumerate(lines):
        for tok in norm(line).split():
            if tok:
                lyric_tokens.append((tok, li))

    # Tokenize transcript words
    transcript_tokens: List[str] = []
    tok2word_idx: List[int] = []
    for wi, w in enumerate(words):
        tok = norm(str(w.get("word", "")))
        if tok:
            transcript_tokens.append(tok)
            tok2word_idx.append(wi)

    L = len(lyric_tokens)
    T = len(transcript_tokens)

    if L == 0 or T == 0:
        # Naive fallback: linear spacing
        print("[yellow]No tokens to DP-align; using naive linear spacing.[/yellow]")
        avg = 2.5
        rows: List[Tuple[int, float, float, str]] = []
        t = 0.0
        for li, text in enumerate(lines):
            rows.append((li, t, t + avg, text))
            t += avg
        return rows

    print(
        f"[cyan]DP alignment:[/cyan] "
        f"[bold]{L}[/bold] lyric tokens vs [bold]{T}[/bold] transcript tokens "
        f"([bold]{n_lines}[/bold] lines)"
    )

    def similar(a: str, b: str) -> bool:
        if not a or not b:
            return False
        return SequenceMatcher(None, a, b).ratio() >= 0.6

    INF = 10**9
    dp = [[INF] * (T + 1) for _ in range(L + 1)]
    prev: List[List[Optional[Tuple[str, int, int]]]] = [
        [None] * (T + 1) for _ in range(L + 1)
    ]
    dp[0][0] = 0

    # Fill DP
    for i in range(L + 1):
        for j in range(T + 1):
            cur = dp[i][j]
            if cur >= INF:
                continue

            # Match / substitution
            if i < L and j < T:
                tok_l, _li = lyric_tokens[i]
                tok_t = transcript_tokens[j]
                cost = 0 if similar(tok_l, tok_t) else 1
                if cur + cost < dp[i + 1][j + 1]:
                    dp[i + 1][j + 1] = cur + cost
                    prev[i + 1][j + 1] = ("M", i, j)

            # Skip transcript token
            if j < T and cur + 1 < dp[i][j + 1]:
                dp[i][j + 1] = cur + 1
                prev[i][j + 1] = ("T", i, j)

            # Skip lyric token
            if i < L and cur + 1 < dp[i + 1][j]:
                dp[i + 1][j] = cur + 1
                prev[i + 1][j] = ("L", i, j)

    if dp[L][T] >= INF:
        print("[yellow]DP alignment failed; falling back to naive spacing.[/yellow]")
        avg = 2.5
        rows = []
        t = 0.0
        for li, text in enumerate(lines):
            rows.append((li, t, t + avg, text))
            t += avg
        return rows

    print(f"[green]DP alignment cost:[/green] [bold]{dp[L][T]}[/bold]")

    # Backtrack to map lyric tokens -> word indices
    line_hits: Dict[int, List[int]] = {}
    i, j = L, T
    while i > 0 or j > 0:
        step = prev[i][j]
        if step is None:
            break
        op, pi, pj = step
        if op == "M":
            tok_l, line_idx = lyric_tokens[pi]
            w_idx = tok2word_idx[pj]
            line_hits.setdefault(line_idx, []).append(w_idx)
        i, j = pi, pj

    # Build per-line ranges (may contain None for missing)
    raw_rows: List[Tuple[int, Optional[float], Optional[float], str]] = []
    for li, text in enumerate(lines):
        hits = line_hits.get(li)
        if hits:
            hits_sorted = sorted(hits)
            starts = [words[h]["start"] for h in hits_sorted if words[h].get("start") is not None]
            ends = [words[h]["end"] for h in hits_sorted if words[h].get("end") is not None]
            if starts and ends:
                s = float(min(starts))
                e = float(max(ends))
                if e <= s:
                    e = s + 0.25
                raw_rows.append((li, s, e, text))
                continue
        # No hits or invalid — placeholder
        raw_rows.append((li, None, None, text))

    # Interpolate missing lines
    out: List[Tuple[int, float, float, str]] = []
    n = len(raw_rows)

    # First pass: fill missing blocks using neighbors where possible
    idx = 0
    while idx < n:
        li, s, e, text = raw_rows[idx]
        if s is not None and e is not None:
            out.append((li, float(s), float(e), text))
            idx += 1
            continue

        # Start of a missing block
        start_block = idx
        while idx < n and (raw_rows[idx][1] is None or raw_rows[idx][2] is None):
            idx += 1
        end_block = idx - 1

        # Neighbors
        left = start_block - 1
        right = idx if idx < n else None

        if left >= 0 and right is not None and right < n:
            # Interpolate between known neighbors
            _, sL, eL, _ = raw_rows[left]
            _, sR, eR, _ = raw_rows[right]
            assert sL is not None and eL is not None and sR is not None and eR is not None
            span = max(0.5, sR - eL)
            count = end_block - start_block + 1
            step = span / (count + 1)
            cur = eL + step
            # Push everything from left side that isn't yet in out
            if len(out) == 0 or out[-1][0] != left:
                out.append((raw_rows[left][0], float(sL), float(eL), raw_rows[left][3]))
            for k in range(start_block, end_block + 1):
                li_k, _s_k, _e_k, tx_k = raw_rows[k]
                s_k = cur
                e_k = s_k + step * 0.9
                out.append((li_k, float(s_k), float(e_k), tx_k))
                cur += step
        elif left >= 0:
            # Only left neighbor known; march forward
            _, sL, eL, _ = raw_rows[left]
            assert sL is not None and eL is not None
            if len(out) == 0 or out[-1][0] != left:
                out.append((raw_rows[left][0], float(sL), float(eL), raw_rows[left][3]))
            cur = eL + 0.3
            for k in range(start_block, end_block + 1):
                li_k, _s_k, _e_k, tx_k = raw_rows[k]
                s_k = cur
                e_k = s_k + 1.0
                out.append((li_k, float(s_k), float(e_k), tx_k))
                cur += 1.3
        elif right is not None and right < n:
            # Only right neighbor known; march backward
            _, sR, eR, _ = raw_rows[right]
            assert sR is not None and eR is not None
            cur_end = sR - 0.3
            for k in range(end_block, start_block - 1, -1):
                li_k, _s_k, _e_k, tx_k = raw_rows[k]
                e_k = cur_end
                s_k = e_k - 1.0
                if s_k < 0:
                    s_k = 0.0
                out.append((li_k, float(s_k), float(e_k), tx_k))
                cur_end = s_k - 0.3
            # We'll add the right neighbor later in normal flow
        else:
            # No neighbors at all (all lines missing) — just space them out
            t = 0.0
            for k in range(start_block, end_block + 1):
                li_k, _s_k, _e_k, tx_k = raw_rows[k]
                out.append((li_k, float(t), float(t + 2.5), tx_k))
                t += 2.5

    # Sort by line_index
    out.sort(key=lambda x: x[0])

    # Enforce monotone non-overlapping times
    MIN_GAP = 0.05
    fixed: List[Tuple[int, float, float, str]] = []
    for idx, (li, s, e, text) in enumerate(out):
        if idx == 0:
            if e <= s:
                e = s + 0.25
            fixed.append((li, s, e, text))
            continue
        pli, ps, pe, ptext = fixed[-1]
        if s < pe + MIN_GAP:
            s = pe + MIN_GAP
        if e <= s:
            e = s + 0.05
        fixed.append((li, s, e, text))

    return fixed


# ----- CLI -----
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto-time lyrics to audio using WhisperX (ASR + wav2vec2 alignment)."
    )
    p.add_argument("--slug", required=True, help="Song slug (used for txts/<slug>.txt, mixes/<slug>_*.wav, mp3s/<slug>.mp3)")
    p.add_argument("--mp3", type=str, help="Explicit audio path (overrides mix/mp3 lookup).")
    p.add_argument("--txt", type=str, help="Explicit lyrics TXT (overrides txts/<slug>.txt).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    slug = args.slug

    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    audio_override = Path(args.mp3) if args.mp3 else None

    print(f"[blue]Slug:[/blue]  [bold]{slug}[/bold]")
    print(f"[blue]TXT:[/blue]   [bold]{txt_path}[/bold]")

    if not txt_path.exists():
        print(f"[bold red]TXT not found:[/bold red] {txt_path}")
        sys.exit(1)

    audio_path = choose_audio(slug, audio_override)
    print(f"[blue]Audio:[/blue] [bold]{audio_path}[/bold]")

    lines = load_lyrics(txt_path)
    print(f"[green]Loaded lyrics lines:[/green] [bold]{len(lines)}[/bold]")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    language = "en"

    # 1) ASR
    asr_result = run_asr(audio_path, language, device)

    # 2) Forced alignment
    aligned = run_alignment(asr_result, audio_path, language, device)
    words = aligned.get("word_segments", [])

    # 3) Map to line timings
    rows = align_lines_dp(lines, words)

    # 4) Write CSV
    write_csv(slug, rows)


if __name__ == "__main__":
    main()

# end of 3_auto_timing_whisperx.py
