#!/usr/bin/env python3
# align_all_in_one_hybrid.py
#
# Hybrid forced alignment for Karaoke Time.
# - Try Gentle (HTTP) first
# - Try WhisperX second
# - Score both
# - Pick best
# - Apply Gentle-style DP line alignment
# - Output canonical 4-column CSV: line_index,start,end,text
#
# Author: Miguel C.
# Hybrid Strategy by ChatGPT
# ---------------------------------------------------------------

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

# ---------------------------------------------------------------
# Color logging via rich
# ---------------------------------------------------------------
try:
    from rich import print  # type: ignore
    from rich.console import Console
    console = Console()
except Exception:  # pragma: no cover
    def print(*args, **kwargs):
        __builtins__["print"](*args, **kwargs)
    console = None

# ---------------------------------------------------------------
# Paths
# ---------------------------------------------------------------
BASE = Path(__file__).resolve().parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
MIXES_DIR = BASE / "mixes"
TIMINGS_DIR = BASE / "timings"
INTERMEDIATE = BASE / "intermediate"
GENTLE_WAV_DIR = INTERMEDIATE / "gentle_audio"

TIMINGS_DIR.mkdir(exist_ok=True, parents=True)
INTERMEDIATE.mkdir(exist_ok=True, parents=True)
GENTLE_WAV_DIR.mkdir(exist_ok=True, parents=True)

# ---------------------------------------------------------------
# External Deps
# ---------------------------------------------------------------
import torch  # type: ignore
import whisperx  # type: ignore
import requests  # type: ignore

# ---------------------------------------------------------------
# Data class
# ---------------------------------------------------------------
@dataclass
class Word:
    text: str
    start: float
    end: float

# ---------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------
_PUNCT_RE = re.compile(r"[^a-z0-9'\s]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

def norm(s: str) -> str:
    s = s.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s)
    return s.strip()

# ---------------------------------------------------------------
# Load lyrics
# ---------------------------------------------------------------
def load_lyrics(txt_path: Path) -> List[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines()]
    return [ln for ln in lines if ln]

# ---------------------------------------------------------------
# Audio selection
# ---------------------------------------------------------------
def choose_audio(slug: str, override: Optional[Path]) -> Path:
    if override and override.exists():
        print(f"[cyan]Using explicit audio:[/cyan] {override}")
        return override

    mixes = list(MIXES_DIR.glob(f"{slug}_*.wav"))
    if mixes:
        best = max(mixes, key=lambda p: p.stat().st_mtime)
        print(f"[green]Using mix audio:[/green] {best}")
        return best

    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        print(f"[yellow]Using mp3:[/yellow] {mp3}")
        return mp3

    print(f"[red]No audio found for slug={slug}[/red]")
    sys.exit(1)

# ---------------------------------------------------------------
# ffmpeg conversion → WAV for Gentle
# ---------------------------------------------------------------
def convert_to_gentle_wav(src: Path) -> Path:
    out = GENTLE_WAV_DIR / f"{src.stem}_gentle.wav"
    if out.exists():
        print(f"[cyan]Reusing Gentle WAV:[/cyan] {out}")
        return out

    print(f"[blue]Converting to Gentle WAV…[/blue]\n  {src} → {out}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-ac", "1",
        "-ar", "16000",
        str(out),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    for line in proc.stderr:
        print(f"[dim cyan]{line.rstrip()}[/dim cyan]")

    proc.wait()
    if proc.returncode != 0:
        print(f"[red]ffmpeg failed[/red]")
        sys.exit(1)

    return out

# ---------------------------------------------------------------
# Gentle HTTP call
# ---------------------------------------------------------------
def call_gentle(audio_wav: Path, transcript: str,
                url="http://localhost:8765/transcriptions?async=false") -> List[Word]:

    if not audio_wav.exists():
        print(f"[red]Gentle WAV missing:{audio_wav}[/red]")
        return []

    print(f"[magenta]→ Calling Gentle…[/magenta]")

    files = {
        "audio": ("audio.wav", audio_wav.open("rb"), "audio/wav")
    }
    data = {"transcript": transcript}

    try:
        resp = requests.post(url, files=files, data=data, timeout=600)
    except Exception as e:
        print(f"[red]Gentle unreachable: {e}[/red]")
        return []

    if resp.status_code != 200:
        print(f"[red]Gentle returned {resp.status_code}[/red]")
        return []

    try:
        payload = resp.json()
    except Exception:
        print("[red]Gentle JSON parse error[/red]")
        return []

    words: List[Word] = []
    for w in payload.get("words", []):
        if w.get("case") != "success":
            continue
        s, e = w.get("start"), w.get("end")
        text = w.get("alignedWord") or w.get("word") or ""
        if s is None or e is None or not text:
            continue
        words.append(Word(text=text, start=float(s), end=float(e)))

    print(f"[green]Gentle aligned {len(words)} words[/green]")
    return words

# ---------------------------------------------------------------
# WhisperX ASR
# ---------------------------------------------------------------
def run_whisperx_asr(audio: Path, device: str) -> dict:
    print("[magenta]→ Loading WhisperX (small.en, int8)…[/magenta]")
    model = whisperx.load_model(
        "small.en",
        device=device,
        compute_type="int8",
    )
    print(f"[cyan]Running ASR on {audio}…[/cyan]")
    result = model.transcribe(str(audio), batch_size=8)
    return result

# ---------------------------------------------------------------
# WhisperX aligner
# ---------------------------------------------------------------
def run_whisperx_align(asr_result: dict, audio: Path, device: str, lang="en") -> List[Word]:
    print("[magenta]→ Loading wav2vec2 aligner…[/magenta]")
    align_model, metadata = whisperx.load_align_model(
        language_code=lang,
        device=device,
    )
    print(f"[cyan]Aligning with wav2vec2 on {audio}…[/cyan]")

    aligned = whisperx.align(
        asr_result["segments"],
        align_model,
        metadata,
        str(audio),
        device=device,
    )

    words_raw = aligned.get("word_segments", [])
    words: List[Word] = []
    for w in words_raw:
        try:
            text = str(w.get("word") or "").strip()
            s = w.get("start")
            e = w.get("end")
            if text and s is not None and e is not None:
                words.append(Word(text=text, start=float(s), end=float(e)))
        except Exception:
            pass

    print(f"[green]WhisperX aligned {len(words)} words[/green]")
    return words

# ---------------------------------------------------------------
# Scoring: choose best alignment
# ---------------------------------------------------------------
def score_alignment(words: List[Word], audio_duration: float) -> float:
    if not words:
        return 0.0

    num = len(words)
    starts = [w.start for w in words]
    ends = [w.end for w in words]
    cov = (max(ends) - min(starts)) / max(1e-6, audio_duration)

    # continuity: how evenly spaced words are
    continuity = num / (max(1, num))

    # Score weighting
    score = (num * 1.0) + (cov * 100.0) + (continuity * 20.0)
    return score

# ---------------------------------------------------------------
# DP alignment (Gentle-style)
# ---------------------------------------------------------------
def dp_align(lines: List[str], words: List[Word]) -> List[Tuple[int, float, float, str]]:
    """
    This is a trimmed + stable version of your DP line alignment.
    Same logic, simplified for hybrid system.
    """
    if not lines:
        return []
    if not words:
        # fallback linear spacing
        rows = []
        t = 0.0
        for i, line in enumerate(lines):
            rows.append((i, t, t + 2.5, line))
            t += 2.5
        return rows

    # Tokenize lyrics with line indices
    lyric_tokens: List[Tuple[str, int]] = []
    for li, ln in enumerate(lines):
        for tok in norm(ln).split():
            lyric_tokens.append((tok, li))

    # Tokenize words
    transcript_tokens: List[str] = []
    tok2idx: List[int] = []
    for wi, w in enumerate(words):
        tok = norm(w.text)
        if tok:
            transcript_tokens.append(tok)
            tok2idx.append(wi)

    L = len(lyric_tokens)
    T = len(transcript_tokens)

    if L == 0 or T == 0:
        return [(i, i*2.5, i*2.5+2.5, ln) for i, ln in enumerate(lines)]

    import numpy as np
    INF = 10**9
    dp = np.full((L+1, T+1), INF, dtype=int)
    prev = [[None]*(T+1) for _ in range(L+1)]
    dp[0][0] = 0

    def similar(a: str, b: str) -> bool:
        if not a or not b:
            return False
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio() >= 0.6

    for i in range(L+1):
        for j in range(T+1):
            cur = dp[i][j]
            if cur >= INF:
                continue

            if i < L and j < T:
                tok_l, _li = lyric_tokens[i]
                tok_t = transcript_tokens[j]
                cost = 0 if similar(tok_l, tok_t) else 1
                if cur + cost < dp[i+1][j+1]:
                    dp[i+1][j+1] = cur + cost
                    prev[i+1][j+1] = ("M", i, j)

            if j < T and cur + 1 < dp[i][j+1]:
                dp[i][j+1] = cur + 1
                prev[i][j+1] = ("T", i, j)

            if i < L and cur + 1 < dp[i+1][j]:
                dp[i+1][j] = cur + 1
                prev[i+1][j] = ("L", i, j)

    if dp[L][T] >= INF:
        return [(i, i*2.5, i*2.5+2.5, ln) for i, ln in enumerate(lines)]

    # Backtrack
    line_hits: Dict[int, List[int]] = {}
    i, j = L, T
    while i > 0 or j > 0:
        step = prev[i][j]
        if not step:
            break
        op, pi, pj = step
        if op == "M":
            tok_l, line_idx = lyric_tokens[pi]
            word_idx = tok2idx[pj]
            line_hits.setdefault(line_idx, []).append(word_idx)
        i, j = pi, pj

    # Build raw lines with None holes
    raw: List[Tuple[int, Optional[float], Optional[float], str]] = []
    for li, text in enumerate(lines):
        hits = line_hits.get(li)
        if hits:
            hits = sorted(hits)
            s = min(words[h].start for h in hits)
            e = max(words[h].end for h in hits)
            raw.append((li, s, e, text))
        else:
            raw.append((li, None, None, text))

    # Fill missing
    out: List[Tuple[int, float, float, str]] = []
    n = len(raw)
    MIN_GAP = 0.05

    # first pass
    idx = 0
    while idx < n:
        li, s, e, text = raw[idx]
        if s is not None and e is not None:
            out.append((li, s, e, text))
            idx += 1
            continue
        # missing block
        start_block = idx
        while idx < n and (raw[idx][1] is None):
            idx += 1
        end_block = idx - 1

        left = start_block - 1
        right = idx if idx < n else None

        if left >= 0 and right is not None and right < n:
            # interpolate between neighbors
            _, sL, eL, _ = raw[left]
            _, sR, eR, _ = raw[right]
            span = max(0.5, sR - eL)
            count = end_block - start_block + 1
            step = span / (count + 1)
            cur = eL + step

            if len(out) == 0 or out[-1][0] != left:
                out.append((raw[left][0], float(sL), float(eL), raw[left][3]))

            for k in range(start_block, end_block+1):
                li_k, _s, _e, tx = raw[k]
                s_k = cur
                e_k = s_k + step*0.8
                out.append((li_k, s_k, e_k, tx))
                cur += step
        elif left >= 0:
            # march from left
            _, sL, eL, _ = raw[left]
            if len(out) == 0 or out[-1][0] != left:
                out.append((raw[left][0], sL, eL, raw[left][3]))
            cur = eL + 0.3
            for k in range(start_block, end_block+1):
                li_k, _, _, tx = raw[k]
                s_k = cur
                e_k = s_k + 1.0
                out.append((li_k, s_k, e_k, tx))
                cur += 1.3
        elif right is not None and right < n:
            # march backward from right
            _, sR, eR, _ = raw[right]
            cur_end = sR - 0.3
            block_rows = []
            for k in range(end_block, start_block-1, -1):
                li_k, _, _, tx = raw[k]
                e_k = cur_end
                s_k = max(0.0, e_k - 1.0)
                block_rows.append((li_k, s_k, e_k, tx))
                cur_end = s_k - 0.3
            out.extend(reversed(block_rows))
        else:
            # no neighbors at all
            t = 0.0
            for k in range(start_block, end_block+1):
                li_k, _, _, tx = raw[k]
                out.append((li_k, t, t+2.5, tx))
                t += 2.5

    # monotonic enforcement
    out.sort(key=lambda x: x[0])
    fixed = []
    for idx, (li, s, e, text) in enumerate(out):
        if idx == 0:
            if e <= s:
                e = s + 0.1
            fixed.append((li, s, e, text))
            continue
        pli, ps, pe, ptxt = fixed[-1]
        if s < pe + MIN_GAP:
            s = pe + MIN_GAP
        if e <= s:
            e = s + 0.1
        fixed.append((li, s, e, text))

    return fixed

# ---------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------
def write_csv(slug: str, rows: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, s, e, text in rows:
            w.writerow([li, f"{s:.3f}", f"{e:.3f}", text])
    print(f"[green]Wrote CSV:[/green] {out}")
    return out

# ---------------------------------------------------------------
# Main hybrid pipeline
# ---------------------------------------------------------------
def run_hybrid(slug: str, mp3_override: Optional[str], txt_override: Optional[str]):
    txt = Path(txt_override) if txt_override else TXT_DIR / f"{slug}.txt"
    if not txt.exists():
        print(f"[red]TXT missing:{txt}[/red]")
        sys.exit(1)

    lines = load_lyrics(txt)
    print(f"[green]Loaded {len(lines)} lyric lines[/green]")

    audio = choose_audio(slug, Path(mp3_override) if mp3_override else None)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Duration (for scoring)
    try:
        dur = float(subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio)
        ], text=True).strip())
    except Exception:
        dur = 0.0

    # -----------------------------------------------------------
    # Gentle path
    # -----------------------------------------------------------
    print("[bold blue]=== Attempting Gentle alignment ===[/bold blue]")
    gentle_wav = convert_to_gentle_wav(audio)
    transcript = txt.read_text(encoding="utf-8", errors="ignore")
    g_words = call_gentle(gentle_wav, transcript)
    score_g = score_alignment(g_words, dur)
    print(f"[cyan]Gentle score: {score_g:.2f}[/cyan]")

    # -----------------------------------------------------------
    # WhisperX path
    # -----------------------------------------------------------
    print("\n[bold blue]=== Attempting WhisperX alignment ===[/bold blue]")
    asr_result = run_whisperx_asr(audio, device)
    w_words = run_whisperx_align(asr_result, audio, device)
    score_w = score_alignment(w_words, dur)
    print(f"[cyan]WhisperX score: {score_w:.2f}[/cyan]")

    # -----------------------------------------------------------
    # Pick best
    # -----------------------------------------------------------
    if score_g >= score_w:
        print("[bold green]→ Selecting Gentle result[/bold green]")
        rows = dp_align(lines, g_words)
    else:
        print("[bold green]→ Selecting WhisperX result[/bold green]")
        rows = dp_align(lines, w_words)

    return write_csv(slug, rows)

# ---------------------------------------------------------------
# CLI
# ---------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Hybrid Forced Alignment: Gentle + WhisperX")
    p.add_argument("--slug", required=True)
    p.add_argument("--mp3", type=str, help="Explicit audio")
    p.add_argument("--txt", type=str, help="Explicit TXT")
    return p.parse_args()

# ---------------------------------------------------------------
# Entry
# ---------------------------------------------------------------
if __name__ == "__main__":
    args = parse_args()
    run_hybrid(args.slug, args.mp3, args.txt)
