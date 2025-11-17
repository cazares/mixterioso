#!/usr/bin/env python3
# scripts/align_vw3.py
#
# VW3 Hybrid Timing Engine (Policy 1: Intersection-first)
#
# Pipeline:
#   1) Load lyrics & pick audio (mp3 preferred)
#   2) Load RMS Vocal Windows (A1)
#   3) Run Lightweight ASR → Word Windows (B1)
#   4) Merge → Intersection-first, Union fallback
#   5) DP-align lines to ASR words with window constraints
#   6) Enforce monotone, drift-proof timings
#   7) Output canonical 4-column CSV:
#          line_index,start,end,text
#
# Zero external servers. Runs entirely local. CPU-friendly.

from __future__ import annotations

import argparse
import json
import csv
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple, Dict, Optional

# ------------------ Color ------------------
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAG = "\033[35m"

def log(tag: str, msg: str, color: str = RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}")

# ------------------ Paths ------------------
BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
WAV_DIR = BASE / "wavs"
META_DIR = BASE / "meta"
TIMINGS_DIR = BASE / "timings"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------ Small utils ------------------
def slugify(s: str) -> str:
    import re
    s = s.strip().lower()
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
    except Exception:
        return 0.0

def load_txt_lines(txt: Path) -> List[str]:
    lines = []
    for ln in txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s:
            lines.append(s)
    return lines

def pick_audio(slug: str) -> Path:
    """
    EXACT behavior you requested:
    - Prefer mp3s/<slug>.mp3   (sounds best)
    - Otherwise use wavs/<slug>.wav
    """
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        log("AUDIO", f"Using MP3 (preferred): {mp3}", GREEN)
        return mp3

    wav = WAV_DIR / f"{slug}.wav"
    if wav.exists():
        log("AUDIO", f"Using WAV fallback: {wav}", YELLOW)
        return wav

    log("AUDIO", f"No audio found for slug={slug}", RED)
    sys.exit(1)

# ------------------ Normalize tokens ------------------
_WORD_RE = re.compile(r"[a-z0-9']+", re.IGNORECASE)
def norm_tokens(s: str) -> List[str]:
    return _WORD_RE.findall(s.lower())
# ------------------ A1: Load RMS Vocal Windows ------------------

def load_rms_windows(slug: str) -> List[Tuple[float, float]]:
    """
    Loads windows from:
        meta/<slug>_vocal_windows.json
    Expected schema:
        {
            "windows": [[s1,e1], [s2,e2], ...],
            "duration": ...
        }

    Returns sorted, non-overlapping float tuples.
    """
    path = META_DIR / f"{slug}_vocal_windows.json"
    if not path.exists():
        log("A1", f"No RMS windows found: {path}", YELLOW)
        return []

    try:
        payload = json.loads(path.read_text())
    except Exception as e:
        log("A1", f"JSON parse error: {e}", RED)
        return []

    raw = payload.get("windows", [])
    if not isinstance(raw, list):
        log("A1", "Invalid RMS windows format", RED)
        return []

    # Convert to float tuples
    windows: List[Tuple[float, float]] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        try:
            s = float(pair[0]); e = float(pair[1])
            if e > s and s >= 0:
                windows.append((s, e))
        except Exception:
            continue

    # Sort & merge any tiny overlaps
    windows.sort(key=lambda w: w[0])
    merged: List[Tuple[float, float]] = []
    for s, e in windows:
        if not merged:
            merged.append((s, e))
            continue
        ps, pe = merged[-1]
        if s <= pe + 1e-6:  # tiny overlap/adjacency
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))

    log("A1", f"Loaded {len(merged)} RMS windows", GREEN)
    for i, (s, e) in enumerate(merged[:8]):
        log("A1", f"  RMS[{i}] = {s:.3f}-{e:.3f}", CYAN)

    return merged
# ------------------ B1: ASR Word Windows (from quick ASR pass) ------------------

def asr_word_windows(
    audio_path: Path,
    model_size: str = "small",
    lang: str = "en",
    device: str = "auto",
    vad: bool = False,
    merge_gap: float = 0.25,
    min_dur: float = 0.30,
) -> List[Tuple[float, float]]:
    """
    Produces coarse ASR 'speech windows' by grouping word timestamps.
    - audio_path: mp3 or vocals stem
    - merge_gap: merge windows separated by <= this
    - min_dur: keep windows only if >= this duration
    """

    log("B1", f"Quick ASR for activity windows | model={model_size}", CYAN)
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type="auto")

    segments, _ = model.transcribe(
        str(audio_path),
        language=(lang or None),
        vad_filter=vad,  # normally False here (bypass VAD)
        word_timestamps=True,
        beam_size=3,
        temperature=0.0,
        no_speech_threshold=0.35,
    )

    # Extract word-level timestamps
    times: List[Tuple[float, float]] = []
    for seg in segments:
        if not getattr(seg, "words", None):
            continue
        for w in seg.words:
            if w.start is not None and w.end is not None:
                times.append((float(w.start), float(w.end)))

    if not times:
        log("B1", "No ASR words detected — returning empty window list", YELLOW)
        return []

    # Sort by start time
    times.sort(key=lambda t: t[0])

    # Group into windows
    windows: List[Tuple[float, float]] = []
    cur_s, cur_e = times[0]

    for s, e in times[1:]:
        if s <= cur_e + merge_gap:
            # Extend the window
            cur_e = max(cur_e, e)
        else:
            # Finalize old window
            if (cur_e - cur_s) >= min_dur:
                windows.append((cur_s, cur_e))
            cur_s, cur_e = s, e

    # Last one
    if (cur_e - cur_s) >= min_dur:
        windows.append((cur_s, cur_e))

    log("B1", f"Found {len(windows)} ASR windows", GREEN)
    for i, (s, e) in enumerate(windows[:8]):
        log("B1", f"  ASR[{i}] = {s:.3f}-{e:.3f}", CYAN)

    return windows
# ------------------ C1: VW3 Unified Windows (merge RMS + ASR) ------------------

def merge_windows(
    rms_windows: List[Tuple[float, float]],
    asr_windows: List[Tuple[float, float]],
    min_gap: float = 0.20,
    min_dur: float = 0.30,
) -> List[Tuple[float, float]]:
    """
    Merge RMS-based windows (A1) and ASR-based windows (B1)
    into unified VW3 windows.

    Behavior:
    - Union of windows from both sources.
    - Sort by start time.
    - Merge if gap <= min_gap.
    - Drop very short windows (< min_dur).
    """

    all_w = []

    for s, e in rms_windows:
        all_w.append((s, e))

    for s, e in asr_windows:
        all_w.append((s, e))

    if not all_w:
        return []

    # Sort by start
    all_w.sort(key=lambda x: x[0])

    merged: List[Tuple[float, float]] = []
    cur_s, cur_e = all_w[0]

    for s, e in all_w[1:]:
        if s <= cur_e + min_gap:
            cur_e = max(cur_e, e)
        else:
            if (cur_e - cur_s) >= min_dur:
                merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e

    # last
    if (cur_e - cur_s) >= min_dur:
        merged.append((cur_s, cur_e))

    # Cleanup pass: ensure sorted, dedup
    merged.sort(key=lambda x: x[0])

    return merged


def log_vw3_preview(unified_windows: List[Tuple[float, float]]):
    """
    Pretty-print first windows for debugging.
    """
    log("VW3", f"Merged unified windows: {len(unified_windows)}", GREEN)
    for i, (s, e) in enumerate(unified_windows[:10]):
        log("VW3", f"  WIN[{i}] = {s:.3f}-{e:.3f} (dur={e-s:.3f})", CYAN)
# ------------------ C2: VW3-guided Line Alignment ------------------

def find_window_for_time(windows: List[Tuple[float, float]], t: float) -> int:
    """
    Return index of window containing t, else -1.
    """
    for i, (s, e) in enumerate(windows):
        if s <= t <= e:
            return i
    return -1


def vw3_guided_alignment(
    lines: List[str],
    words: List[Word],
    vw3_windows: List[Tuple[float, float]],
    coverage_thresh: float = 0.55,
    search_pad: int = 48,
    gap_fallback: float = 1.75,
) -> List[Tuple[int, float, float, str]]:
    """
    Window-aware line alignment:

    Behavior:
    - If a lyric line *must* appear inside a window (speech section), we search only within
      ASR word spans that overlap that VW3 window.
    - If we're between windows (musical gap), schedule line AFTER the next valid window,
      avoiding 'Ria-shaped' premature matches.
    - Strict monotonic progression.
    - Output includes END times (equal to next line start or +0).
    """

    # Pre-extract tokenized words and times
    all_tokens = []
    token_times = []
    for w in words:
        toks = norm_tokens(w.text)
        if not toks:
            continue
        for t in toks:
            all_tokens.append(t)
            token_times.append(w.start)

    N = len(all_tokens)
    if N == 0:
        # Fallback linear spacing
        out = []
        t = 0.0
        for i, line in enumerate(lines):
            out.append((i, t, t + 0.01, line))
            t += gap_fallback
        return out

    # Pre-window mapping: token index -> window index (or -1)
    token_widx = []
    for tt in token_times:
        token_widx.append(find_window_for_time(vw3_windows, tt))

    out = []
    cursor = 0   # monotonic ASR token pointer

    for li, line in enumerate(lines):
        ltoks = norm_tokens(line)

        if not ltoks:
            # empty row: inherit previous start
            prev = out[-1][1] if out else 0.0
            out.append((li, prev, prev + 0.01, line))
            continue

        best = (-1, None, None, None)  # score, j, k, window_index
        # Window-derived search rules ------------------------------------------------

        # Try matching in *any* VW3 window that starts after cursor's current word time
        for win_i, (ws, we) in enumerate(vw3_windows):
            # Restrict search to ASR token indexes within this window
            token_idxs = [
                idx for idx in range(cursor, N)
                if ws <= token_times[idx] <= we
            ]
            if not token_idxs:
                continue
            j_start = token_idxs[0]
            j_end   = token_idxs[-1]

            approx_len = max(1, len(ltoks))

            for j in range(j_start, min(j_end, j_start + search_pad) + 1):
                k_max = min(N, j + approx_len + (approx_len // 2) + 1)
                for k in range(j + approx_len, k_max):
                    window_toks = all_tokens[j:k]
                    if not window_toks:
                        continue
                    hits = sum(1 for t in ltoks if t in window_toks)
                    ratio = hits / len(ltoks)
                    if ratio > best[0]:
                        best = (ratio, j, k, win_i)

        score, j, k, win_i = best

        if score >= coverage_thresh and j is not None:
            # Valid match inside window
            ts = token_times[j]
            out.append((li, ts, ts + 0.01, line))
            cursor = max(cursor, k)
        else:
            # NO match inside windows → enforce “don’t allow early Ria”
            # place line right AFTER the next window
            if vw3_windows:
                # find next window after current cursor time
                cur_t = token_times[cursor] if cursor < N else token_times[-1]
                next_win = None
                for w_i, (ws, we) in enumerate(vw3_windows):
                    if we >= cur_t:
                        next_win = (ws, we)
                        break
                if next_win:
                    we = next_win[1]
                    ts = we + 0.50
                else:
                    # beyond last window → monotonic push
                    ts = (out[-1][1] if out else 0.0) + gap_fallback
            else:
                # no windows at all
                ts = (out[-1][1] if out else 0.0) + gap_fallback

            out.append((li, ts, ts + 0.01, line))
            cursor = min(N - 1, cursor + 3)

    # finalize END times using next line’s start
    fixed = []
    for i, (li, st, en, tx) in enumerate(out):
        if i + 1 < len(out):
            en = max(en, out[i + 1][1] - 0.01)
        fixed.append((li, st, en, tx))

    return fixed
# ------------------ C3: End-Stitch + Canonical CSV/JSON Output ------------------

def stitch_end_times(aligned_rows):
    """
    Input:  list of (line_index, start, end, text) with end often dummy (start+0.01)
    Output: enforce end = next.start - tiny_epsilon
    """
    if not aligned_rows:
        return []

    eps = 0.001
    out = []
    for i, (li, st, en, tx) in enumerate(aligned_rows):
        if i + 1 < len(aligned_rows):
            nxt_st = aligned_rows[i + 1][1]
            en = max(en, nxt_st - eps)
        out.append((li, st, en, tx))
    return out


def write_canonical_csv(path: Path, rows):
    """
    Writes canonical 4-column CSV:
        line_index,start,end,text
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, st, en, tx in rows:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", tx])


def write_debug_json(path: Path, slug: str, words, vw3_windows, aligned):
    """
    Full introspection of:
        - ASR tokens
        - VW3 windows
        - Aligned lines
    """
    token_dump = []
    for w in words:
        toks = norm_tokens(w.text)
        token_dump.append({
            "word": w.text,
            "start": w.start,
            "end": w.end,
            "tokens": toks,
        })

    payload = {
        "slug": slug,
        "version": "VW3-align v1",
        "windows": [{"start": s, "end": e} for (s, e) in vw3_windows],
        "asr_words": token_dump,
        "aligned": [
            {"line_index": li, "start": st, "end": en, "text": tx}
            for (li, st, en, tx) in aligned
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
# ------------------ C3b: FULL ASSEMBLY WRAPPER ------------------

def vw3_full_alignment(
    slug: str,
    mp3_path: Path,
    txt_path: Path,
    tmp_dir: Path,
    out_csv: Path,
    out_debug: Optional[Path] = None,
    model_size: str = "small",
    lang: str = "en",
):
    """
    One-shot wrapper:

        1. RMS windows (A1)
        2. ASR words from vocals or original (B1/B2)
        3. VW3 unified windows (C1)
        4. VW3-aware alignment (C2)
        5. End-stitch + output (C3)

    Produces canonical 4-column CSV.
    """

    tmp_dir.mkdir(parents=True, exist_ok=True)

    # --- A1: Short-time RMS windows
    log("A1", f"Computing RMS windows for {mp3_path}", CYAN)
    rms_windows = compute_rms_windows(mp3_path)

    # --- B1/B2: ASR
    log("ASR", f"Transcribing via Faster-Whisper model={model_size}", CYAN)
    words = asr_words_from_audio(mp3_path, model_size=model_size, lang=lang)

    # --- C1: VW3 unified windows
    log("VW3", "Building VW3 vocal windows", CYAN)
    vw3_windows = build_vw3_windows(rms_windows, words)

    # --- Load lyrics
    lines = read_lyrics_lines(txt_path)
    log("LYR", f"{len(lines)} lyric lines", GREEN)

    # --- C2: Alignment
    log("ALIGN", "Running VW3-guided alignment", CYAN)
    aligned_rows = vw3_guided_alignment(
        lines,
        words,
        vw3_windows,
        coverage_thresh=0.55,
        search_pad=48,
        gap_fallback=1.75,
    )

    # --- C3: Final end-stitch
    aligned_rows = stitch_end_times(aligned_rows)

    # --- Save CSV
    write_canonical_csv(out_csv, aligned_rows)
    log("OUT", f"Wrote canonical CSV: {out_csv}", GREEN)

    # --- Optional debug JSON
    if out_debug:
        write_debug_json(out_debug, slug, words, vw3_windows, aligned_rows)
        log("OUT", f"Wrote debug JSON: {out_debug}", CYAN)

    return aligned_rows
