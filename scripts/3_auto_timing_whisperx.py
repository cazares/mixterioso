#!/usr/bin/env python3
# scripts/3_auto_timing_whisperx.py
#
# Auto-time lyrics using WhisperX (ASR + wav2vec2 alignment).
# CPU-safe for macOS. VAD disabled by default to avoid timeline shrinkage.
# Outputs timings/<slug>.csv with: line_index,start,end,text

from __future__ import annotations
import argparse
import csv
import sys
import re
from pathlib import Path
from typing import List, Tuple, Optional, Dict

try:
    from rich import print
except Exception:
    pass

# Directories
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
MIXES_DIR = BASE_DIR / "mixes"
TIMINGS_DIR = BASE_DIR / "timings"
TIMINGS_DIR.mkdir(exist_ok=True, parents=True)

# WhisperX imports
import whisperx
import torch

# ---------------------- Normalization ----------------------
_PUNCT_RE = re.compile(r"[^a-z0-9'\s]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")

def norm(s: str) -> str:
    s = s.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s

# ---------------------- CSV Writer -------------------------
def write_csv(slug: str, rows: List[Tuple[int, float, float, str]]) -> Path:
    out = TIMINGS_DIR / f"{slug}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, s, e, t in rows:
            w.writerow([li, f"{s:.3f}", f"{e:.3f}", t])
    print(f"[green]Wrote timings:[/green] {out}")
    return out

# ---------------------- Load Lyrics -------------------------
def load_lyrics(txt_path: Path) -> List[str]:
    raw = txt_path.read_text(encoding="utf-8", errors="ignore")
    lines = [ln.strip() for ln in raw.splitlines()]
    return [ln for ln in lines if ln]

# ---------------------- Audio Selection ---------------------
def choose_audio(slug: str, explicit: Optional[Path]) -> Path:
    if explicit and explicit.exists():
        return explicit
    # Prefer mixes/<slug>_*.wav (full mix)
    cand = list(MIXES_DIR.glob(f"{slug}_*.wav"))
    if cand:
        return max(cand, key=lambda p: p.stat().st_mtime)
    # fallback mp3s/<slug>.mp3
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        return mp3
    print(f"[bold red]Audio not found for slug={slug}[/bold red]")
    sys.exit(1)

# ---------------------- Transcribe (WhisperX) ----------------
def run_asr(audio_path: Path, language: str, device: str) -> dict:
    print("[cyan]Loading WhisperX ASR model (CPU int8)...[/cyan]")
    model = whisperx.load_model(
        "small.en" if language == "en" else "small",
        device=device,
        compute_type="int8"
    )

    print(f"[cyan]Running ASR on:[/cyan] {audio_path}")
    result = model.transcribe(str(audio_path), batch_size=8)
    return result

# ---------------------- Alignment Model ----------------------
def run_alignment(asr_result: dict, audio_path: Path, language: str, device: str, use_vad: bool) -> dict:
    print("[cyan]Loading wav2vec2 alignment model...[/cyan]")
    # Correct call: NO compute_type parameter
    align_model, metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
    )

    print("[cyan]Running alignment (VAD disabled unless explicitly enabled)...[/cyan]")
    aligned = whisperx.align(
        asr_result["segments"],
        align_model,
        metadata,
        str(audio_path),
        device=device,
    )
    return aligned

# ---------------------- DP Line Alignment --------------------
def align_lines_dp(lines: List[str], words: List[dict]) -> List[Tuple[int, float, float, str]]:
    if not lines:
        return []

    # Build lyric tokens → (token, line_index)
    lyric_tokens = []
    for li, line in enumerate(lines):
        for tok in norm(line).split():
            if tok:
                lyric_tokens.append((tok, li))

    # Build transcript tokens (normalized)
    transcript_tokens = []
    idx2word = []
    for wi, w in enumerate(words):
        tok = norm(w["word"])
        if tok:
            transcript_tokens.append(tok)
            idx2word.append(wi)

    L = len(lyric_tokens)
    T = len(transcript_tokens)

    if L == 0 or T == 0:
        # naive linear spacing
        avg = 2.5
        rows = []
        t = 0.0
        for li, line in enumerate(lines):
            rows.append((li, t, t + avg, line))
            t += avg
        return rows

    print(f"[cyan]DP alignment: {L} lyric tokens vs {T} transcript tokens[/cyan]")

    # DP matrix
    INF = 10**9
    dp = [[INF] * (T + 1) for _ in range(L + 1)]
    prev = [[None] * (T + 1) for _ in range(L + 1)]
    dp[0][0] = 0

    def similar(a, b):
        return a == b

    # Fill DP
    for i in range(L + 1):
        for j in range(T + 1):
            cur = dp[i][j]
            if cur >= INF:
                continue

            # match
            if i < L and j < T:
                tok_l, _li = lyric_tokens[i]
                tok_t = transcript_tokens[j]
                cost = 0 if similar(tok_l, tok_t) else 1
                if cur + cost < dp[i+1][j+1]:
                    dp[i+1][j+1] = cur + cost
                    prev[i+1][j+1] = ("M", i, j)

            # skip transcript
            if j < T and cur + 1 < dp[i][j+1]:
                dp[i][j+1] = cur + 1
                prev[i][j+1] = ("T", i, j)

            # skip lyric
            if i < L and cur + 1 < dp[i+1][j]:
                dp[i+1][j] = cur + 1
                prev[i+1][j] = ("L", i, j)

    # Backtrack
    line_hits: Dict[int, List[int]] = {}
    i, j = L, T
    while i > 0 or j > 0:
        step = prev[i][j]
        if step is None:
            break
        op, pi, pj = step
        if op == "M":
            _, line_idx = lyric_tokens[pi]
            widx = idx2word[pj]
            line_hits.setdefault(line_idx, []).append(widx)
        i, j = pi, pj

    # Build per-line raw ranges
    rows = []
    for li, line in enumerate(lines):
        if li in line_hits:
            word_ids = sorted(line_hits[li])
            s = words[word_ids[0]]["start"]
            e = words[word_ids[-1]]["end"]
            if e <= s:
                e = s + 0.25
            rows.append((li, s, e, line))
        else:
            rows.append((li, None, None, line))

    # Interpolate missing lines
    out = list(rows)
    n = len(out)

    # forward/backward fill gaps
    for idx in range(n):
        li, s, e, tx = out[idx]
        if s is None:
            # find neighbors
            left = next((k for k in range(idx-1, -1, -1) if out[k][1] is not None), None)
            right = next((k for k in range(idx+1, n) if out[k][1] is not None), None)
            if left is not None and right is not None:
                _, sL, eL, _ = out[left]
                _, sR, eR, _ = out[right]
                span = max(0.5, sR - eL)
                step = span / (right - left)
                s_new = eL + step*(idx - left)
                e_new = s_new + (step*0.9)
                out[idx] = (li, s_new, e_new, tx)
            elif left is not None:
                _, sL, eL, _ = out[left]
                s_new = eL + 0.3
                out[idx] = (li, s_new, s_new+1.0, tx)
            elif right is not None:
                _, sR, eR, _ = out[right]
                e_new = sR - 0.3
                s_new = e_new - 1.0
                if s_new < 0: s_new = 0
                out[idx] = (li, s_new, e_new, tx)
            else:
                out[idx] = (li, idx*2.5, idx*2.5+2.5, tx)

    # enforce monotonic + gaps
    MIN_GAP = 0.05
    out.sort(key=lambda t: t[0])
    for k in range(1, n):
        li, s, e, tx = out[k]
        _, sp, ep, _ = out[k-1]
        if s < ep + MIN_GAP:
            s = ep + MIN_GAP
        if e <= s:
            e = s + 0.01
        out[k] = (li, s, e, tx)

    return out

# ---------------------- MAIN ----------------------
def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--mp3", type=str)
    ap.add_argument("--txt", type=str)
    ap.add_argument("--use-vad", type=int, default=0)
    return ap.parse_args()

def main():
    args = parse_args()
    slug = args.slug

    txt_path = Path(args.txt) if args.txt else (TXT_DIR / f"{slug}.txt")
    explicit_audio = Path(args.mp3) if args.mp3 else None

    if not txt_path.exists():
        print(f"[bold red]TXT missing: {txt_path}[/bold red]")
        sys.exit(1)

    audio_path = choose_audio(slug, explicit_audio)

    print(f"[blue]slug=[/blue] {slug}")
    print(f"[blue]txt=[/blue] {txt_path}")
    print(f"[blue]audio=[/blue] {audio_path}")

    lines = load_lyrics(txt_path)
    print(f"[green]Loaded {len(lines)} lines[/green]")

    device = "cpu"
    language = "en"

    # 1. ASR
    asr_res = run_asr(audio_path, language, device)

    # 2. Alignment (wav2vec2)
    aligned = run_alignment(
        asr_res,
        audio_path,
        language,
        device,
        use_vad=bool(args.use_vad),
    )

    # Extract word-level timestamps
    aligned_words = aligned["word_segments"]

    # 3. DP line alignment
    rows = align_lines_dp(lines, aligned_words)

    # 4. Write CSV
    write_csv(slug, rows)

if __name__ == "__main__":
    main()
# end of 3_auto_timing_whisperx.py
