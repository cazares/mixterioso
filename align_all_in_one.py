#!/usr/bin/env python3
# scripts/align_vw3.py
#
# VW3 Hybrid Timing Engine (A + B + C Policy)
#
#   A1 = RMS Vocal Windows (Demucs optional but strongly recommended)
#   B1 = Faster-Whisper ASR (small model)
#   C1 = VW3 Unified Windows (merge + dedupe)
#   C2 = VW3-Guided DP Line Alignment
#   C3 = End-Stitch → Canonical CSV (line_index,start,end,text)
#
# This is the one-shot “just give me the CSV for 4_mp4” engine.
#
# ---------------------------------------------------------------------

from __future__ import annotations
import argparse
import json
import csv
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple, Optional

# ------------------ Colors ------------------
RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAG = "\033[35m"
BOLD = "\033[1m"
WHITE = "\033[97m"

def log(tag: str, msg: str, color: str = RESET):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}")

# ------------------ Paths ------------------
BASE = Path(__file__).resolve().parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
WAV_DIR = BASE / "wavs"
META_DIR = BASE / "meta"
TIMINGS_DIR = BASE / "timings"
TMP_DIR = BASE / "tmp_vw3"
TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

# ------------------ Helpers ------------------
# === TIMING SANITY FILTERS ===
def sanitize_timings(rows, song_duration, merge_ms=150):
    """
    rows = list of dicts with: index, start, end, text
    Returns a new cleaned list with:
      - merged near-duplicates
      - strictly increasing start times
      - end <= next.start - epsilon
      - no rows past song end
    """

    # ---------- 1. Merge lyrics within merge_ms ----------
    merged = []
    cur = None
    merge_s = merge_ms / 1000.0

    for row in rows:
        if cur is None:
            cur = row.copy()
            continue

        # if start times are near-duplicates → merge
        if abs(row["start"] - cur["start"]) <= merge_s and row["text"] == cur["text"]:
            cur["end"] = max(cur["end"], row["end"])
        else:
            merged.append(cur)
            cur = row.copy()

    if cur is not None:
        merged.append(cur)

    rows = merged

    # ---------- 2. Sort by start time ----------
    rows.sort(key=lambda r: r["start"])

    # ---------- 3. Clamp to song duration ----------
    clean = []
    for r in rows:
        if r["start"] >= song_duration:
            continue
        r2 = r.copy()
        r2["end"] = min(r["end"], song_duration - 0.10)
        clean.append(r2)

    rows = clean

    # ---------- 4. Strictly increasing start times ----------
    for i in range(1, len(rows)):
        if rows[i]["start"] <= rows[i-1]["start"]:
            rows[i]["start"] = rows[i-1]["start"] + 0.001

    # ---------- 5. Force end = next.start - epsilon ----------
    eps = 0.05
    for i in range(len(rows)-1):
        rows[i]["end"] = min(rows[i]["end"], rows[i+1]["start"] - eps)

    # Final clamp
    rows[-1]["end"] = min(rows[-1]["end"], song_duration - 0.10)

    return rows

def norm_tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", s.lower())

def read_lyrics_lines(path: Path) -> List[str]:
    lines = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = ln.strip()
        if s:
            lines.append(s)
    return lines

def slugify(x: str) -> str:
    s = x.lower().strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w\-]", "", s)
    return s or "song"

def pick_audio(slug: str) -> Path:
    """Prefer MP3. Fallback to WAV."""
    mp3 = MP3_DIR / f"{slug}.mp3"
    if mp3.exists():
        log("AUDIO", f"Using MP3: {mp3}", GREEN)
        return mp3
    wav = WAV_DIR / f"{slug}.wav"
    if wav.exists():
        log("AUDIO", f"Using WAV fallback: {wav}", YELLOW)
        return wav
    log("AUDIO", "No audio found.", RED)
    sys.exit(1)

# ------------------ A1: RMS WINDOW ESTIMATION ------------------
def compute_rms_windows(audio_path: Path,
                        window_ms: int = 200,
                        hop_ms: int = 80,
                        rms_thresh: float = 0.015,
                        min_dur: float = 0.30) -> List[Tuple[float, float]]:
    """
    Lightweight RMS-based singing-activity detector.
    Does NOT require Demucs, but supports it.
    """

    log("A1", f"Computing RMS windows (window={window_ms}ms hop={hop_ms}ms)", CYAN)

    try:
        import librosa
        import numpy as np
    except ImportError:
        log("A1", "librosa missing — pip install librosa", RED)
        return []

    y, sr = librosa.load(str(audio_path), sr=None, mono=True)

    # Compute RMS
    frame_length = int(sr * window_ms / 1000)
    hop_length   = int(sr * hop_ms / 1000)
    rms = librosa.feature.rms(y=y,
                              frame_length=frame_length,
                              hop_length=hop_length)[0]

    times = librosa.frames_to_time(range(len(rms)), sr=sr,
                                   hop_length=hop_length,
                                   n_fft=frame_length)

    raw = []
    in_region = False
    s = 0.0

    for t, r in zip(times, rms):
        if r >= rms_thresh and not in_region:
            in_region = True
            s = t
        elif r < rms_thresh and in_region:
            in_region = False
            e = t
            if e - s >= min_dur:
                raw.append((s, e))

    # If ended inside window
    if in_region:
        e = times[-1]
        if e - s >= min_dur:
            raw.append((s, e))

    log("A1", f"RMS raw windows detected = {len(raw)}", GREEN)
    for i, (rs, re_) in enumerate(raw[:8]):
        log("A1", f"  RMS[{i}] = {rs:.3f}-{re_:.3f}", WHITE)

    return raw

# ------------------ B1: ASR WORDS USING FASTER-WHISPER ------------------
class Word:
    def __init__(self, text: str, start: float, end: float):
        self.text = text
        self.start = start
        self.end = end

def asr_words_from_audio(path: Path,
                         model_size: str = "small",
                         lang: str = "en") -> List[Word]:
    log("ASR", f"Loading Faster-Whisper ({model_size}) …", CYAN)

    from faster_whisper import WhisperModel
    model = WhisperModel(model_size, compute_type="int8", device="cpu")

    log("ASR", "Transcribing (word timestamps ON, VAD disabled) …", MAG)

    segments, _ = model.transcribe(
        str(path),
        language=(lang or None),
        beam_size=3,
        word_timestamps=True,
        vad_filter=False,
        no_speech_threshold=0.35,
        temperature=0.0
    )

    out = []
    for seg in segments:
        if not hasattr(seg, "words") or not seg.words:
            continue
        for w in seg.words:
            if w.start is None or w.end is None:
                continue
            out.append(Word(w.word, float(w.start), float(w.end)))

    log("ASR", f"ASR words = {len(out)}", GREEN)
    for i, w in enumerate(out[:10]):
        log("ASR", f"  WORD[{i}] '{w.text}' {w.start:.3f}-{w.end:.3f}", WHITE)

    return out

# ------------------ C1: VW3 MERGING ------------------
def build_vw3_windows(
    rms_windows: List[Tuple[float, float]],
    words: List[Word],
    min_gap: float = 0.20,
    min_dur: float = 0.30,
) -> List[Tuple[float, float]]:

    log("VW3", "Merging RMS + ASR word-based windows …", CYAN)

    # Convert ASR words → windows
    word_times = [(w.start, w.end) for w in words]
    word_times.sort(key=lambda x: x[0])

    merged = []
    for s, e in rms_windows:
        merged.append((s, e))
    for s, e in word_times:
        merged.append((s, e))

    if not merged:
        return []

    merged.sort(key=lambda x: x[0])
    out = []
    cs, ce = merged[0]

    for s, e in merged[1:]:
        if s <= ce + min_gap:
            ce = max(ce, e)
        else:
            if ce - cs >= min_dur:
                out.append((cs, ce))
            cs, ce = s, e

    if ce - cs >= min_dur:
        out.append((cs, ce))

    log("VW3", f"Unified windows: {len(out)}", GREEN)
    for i, (s, e) in enumerate(out[:10]):
        log("VW3", f"  WIN[{i}] {s:.3f}-{e:.3f} dur={e-s:.3f}", WHITE)

    return out

# ------------------ C2: VW3-GUIDED ALIGNMENT ------------------
def vw3_guided_alignment(lines, words, windows,
                         coverage_thresh=0.55,
                         search_pad=48,
                         gap_fallback=1.75):

    log("ALIGN", "Running VW3-guided alignment …", CYAN)

    # Flatten ASR into token list
    tokens = []
    times = []
    for w in words:
        ts = norm_tokens(w.text)
        for t in ts:
            tokens.append(t)
            times.append(w.start)

    N = len(tokens)
    out = []
    cursor = 0

    def find_win(t):
        for i, (s, e) in enumerate(windows):
            if s <= t <= e:
                return i
        return -1

    for i, line in enumerate(lines):
        ltoks = norm_tokens(line)
        if not ltoks:
            prev = out[-1][1] if out else 0.0
            out.append((i, prev, prev + 0.01, line))
            continue

        best = (-1, None, None)
        # Search inside VW3 windows
        for (ws, we) in windows:
            # restrict tokens that start inside this window
            idxs = [k for k in range(cursor, N)
                    if ws <= times[k] <= we]
            if not idxs:
                continue
            j0, j1 = idxs[0], idxs[-1]

            approx = max(1, len(ltoks))

            for j in range(j0, min(j1, j0 + search_pad) + 1):
                kmax = min(N, j + approx + approx // 2 + 1)
                for k in range(j + approx, kmax):
                    window_toks = tokens[j:k]
                    hits = sum(1 for t in ltoks if t in window_toks)
                    ratio = hits / len(ltoks)
                    if ratio > best[0]:
                        best = (ratio, j, k)

        score, j, k = best

        if score >= coverage_thresh and j is not None:
            ts = times[j]
            out.append((i, ts, ts + 0.01, line))
            cursor = max(cursor, k)
        else:
            # fallback — place line AFTER next window, not before
            if windows:
                cur_t = times[cursor] if cursor < N else times[-1]
                next_w = None
                for (ws, we) in windows:
                    if we >= cur_t:
                        next_w = (ws, we)
                        break
                if next_w:
                    st = next_w[1] + 0.50
                else:
                    st = out[-1][1] + gap_fallback if out else 0.0
            else:
                st = out[-1][1] + gap_fallback if out else 0.0

            out.append((i, st, st + 0.01, line))
            cursor = min(N - 1, cursor + 3)

    return out

# ------------------ C3: END-STITCH ------------------
def stitch_end_times(rows):
    if not rows:
        return []
    eps = 0.001
    out = []
    for i, (li, st, en, tx) in enumerate(rows):
        if i + 1 < len(rows):
            en = max(en, rows[i + 1][1] - eps)
        out.append((li, st, en, tx))
    return out

def write_canonical_csv(path: Path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, st, en, tx in rows:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", tx])

# ------------------ MAIN PIPELINE ------------------
def run_vw3(slug: str, debug: bool = False):
    log("MAIN", f"VW3 ALIGNMENT START for slug={slug}", BOLD + CYAN)

    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("MAIN", f"Missing txt file: {txt_path}", RED)
        sys.exit(1)

    audio = pick_audio(slug)

    lines = read_lyrics_lines(txt_path)
    log("LYR", f"Loaded {len(lines)} lyric lines", GREEN)

    # A1
    log("A1", WHITE + BOLD + "*** RMS DETECTION (Demucs optional) ***")
    rms = compute_rms_windows(audio)

    # B1
    words = asr_words_from_audio(audio)

    # C1
    windows = build_vw3_windows(rms, words)

    # C2
    aligned = vw3_guided_alignment(lines, words, windows)

    # C3
    aligned = stitch_end_times(aligned)

        # === SANITIZE TIMINGS ===
    # Convert `(li, st, en, tx)` tuples → dict rows
    dict_rows = []
    for (li, st, en, tx) in aligned:
        dict_rows.append({
            "index": li,
            "start": float(st),
            "end": float(en),
            "text": tx
        })

    import librosa
    # Load audio again just to get true duration (fast)
    y, sr = librosa.load(str(audio), sr=None, mono=True)
    duration = len(y) / sr

    log("SANITY", f"Applying timing sanitizer to {len(dict_rows)} rows…", WHITE + BOLD)
    cleaned = sanitize_timings(dict_rows, duration)
    log("SANITY", f"→ {len(cleaned)} rows after cleaning", GREEN + BOLD)

    # Convert dict rows → canonical tuple rows
    aligned = []
    for r in cleaned:
        aligned.append((
            r["index"],
            r["start"],
            r["end"],
            r["text"]
        ))

    out_csv = TIMINGS_DIR / f"{slug}.csv"
    write_canonical_csv(out_csv, aligned)
    log("OUT", f"WROTE CSV: {out_csv}", GREEN + BOLD)

    if debug:
        dbg = META_DIR / f"{slug}_vw3_debug.json"
        payload = {
            "slug": slug,
            "windows": [{"start": s, "end": e} for (s, e) in windows],
            "asr_words": [
                {
                    "word": w.text,
                    "start": w.start,
                    "end": w.end,
                    "tokens": norm_tokens(w.text)
                }
                for w in words
            ],
            "aligned": [
                {"line_index": li, "start": st, "end": en, "text": tx}
                for (li, st, en, tx) in aligned
            ],
        }
        dbg.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log("OUT", f"WROTE DEBUG JSON: {dbg}", CYAN)

    log("MAIN", "VW3 DONE", GREEN + BOLD)


# CLI
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--debug", action="store_true")
    a = p.parse_args()
    run_vw3(a.slug, debug=a.debug)
