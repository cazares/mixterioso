#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# MINIMALIST AUTO-TIMING ENGINE (WHISPERX)
#
# Purpose:
#   - Take txts/<slug>.txt and audio for <slug>
#   - Run WhisperX ASR + forced alignment
#   - Align lyric lines using strict token ordering
#   - Produce stable, predictable CSV timings
#
# Canonical CSV format for 4_mp4.py:
#   line_index,start,end,text
#
# ---------------------------------------------------------------------------

from __future__ import annotations
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple, Any

# ------------------ Colors ------------------
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
WHITE = "\033[97m"


def log(tag: str, msg: str, color: str = RESET) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{tag}] {msg}{RESET}")


# ------------------ Paths ------------------
BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MIXES_DIR = BASE / "mixes"
MP3_DIR = BASE / "mp3s"
WAV_DIR = BASE / "wavs"
TIMINGS_DIR = BASE / "timings"
META_DIR = BASE / "meta"

TIMINGS_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)


# ------------------ Helpers ------------------

def norm_tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def read_lyrics(path: Path) -> List[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def pick_audio(slug: str) -> Path:
    for p in [
        MIXES_DIR / f"{slug}_karaoke.wav",
        MIXES_DIR / f"{slug}.wav",
        MP3_DIR / f"{slug}.mp3",
        WAV_DIR / f"{slug}.wav",
    ]:
        if p.exists():
            log("AUDIO", f"Using: {p}", GREEN)
            return p
    log("AUDIO", f"No audio found for slug={slug}", RED)
    sys.exit(1)


def audio_duration(path: Path) -> float:
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=None, mono=True)
        return len(y) / float(sr)
    except Exception:
        return 0.0


# ------------------ WhisperX ------------------

def run_whisperx(audio_path: Path, lang: str = "en") -> Dict[str, Any]:
    log("WX", "Loading WhisperX (medium)…", CYAN)

    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    model = whisperx.load_model("medium", device=device, compute_type=compute_type)
    log("WX", f"Transcribing {audio_path}", CYAN)
    result = model.transcribe(str(audio_path), batch_size=16)

    model_a, metadata = whisperx.load_align_model(
        language_code=result.get("language", lang),
        device=device,
    )

    aligned = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        str(audio_path),
        device=device,
    )

    word_segments = aligned.get("word_segments") or []
    log("WX", f"Word segments: {len(word_segments)}", GREEN)

    return {"segments": result.get("segments", []), "word_segments": word_segments}


# ------------------ Token extraction ------------------

def extract_tokens(word_segments: List[Dict[str, Any]]) -> Tuple[List[str], List[float]]:
    tokens, times = [], []
    for w in word_segments:
        start = float(w.get("start", 0.0))
        for t in norm_tokens(w.get("word", "")):
            tokens.append(t)
            times.append(start)
    return tokens, times


# ------------------ Minimalist line alignment ------------------

def align_lines(
    lines: List[str],
    tokens: List[str],
    token_times: List[float],
    search_pad: int = 30,
    fallback_gap: float = 0.5,
) -> List[Tuple[int, float, float, str]]:

    log("ALIGN", "Strict sequence alignment…", CYAN)

    N = len(tokens)
    out = []
    cursor = 0

    for idx, line in enumerate(lines):
        ltoks = norm_tokens(line)
        if not ltoks:
            base = out[-1][1] if out else token_times[0]
            out.append((idx, base, base + 0.01, line))
            continue

        best_j = None
        best_score = -1.0

        j_end = min(N - len(ltoks), cursor + search_pad)

        for j in range(cursor, j_end + 1):
            if tokens[j: j + len(ltoks)] == ltoks:
                best_j = j
                best_score = 1.0
                break

        if best_score > 0 and best_j is not None:
            st = token_times[best_j]
            out.append((idx, st, st + 0.01, line))
            cursor = best_j + len(ltoks)
        else:
            base = out[-1][1] + fallback_gap if out else token_times[0]
            out.append((idx, base, base + 0.01, line))
            cursor = min(N - 1, cursor + 1)

    return out


# ------------------ Minimalist timing sanitizer ------------------

def sanitize(rows: List[Tuple[int, float, float, str]], song_dur: float) -> List[Tuple[int, float, float, str]]:
    if not rows:
        return rows

    cleaned = []

    # Clamp & monotone starts
    prev_start = 0.0
    for li, st, en, tx in rows:
        st = max(st, prev_start)
        en = min(en, song_dur - 0.05)
        cleaned.append((li, st, en, tx))
        prev_start = st + 0.001

    # Expand durations
    EPS = 0.10
    expanded = []

    for i in range(len(cleaned) - 1):
        li, st, en, tx = cleaned[i]
        next_st = cleaned[i + 1][1]
        expanded.append((li, st, max(st + 0.01, next_st - EPS), tx))

    # Last line
    li, st, en, tx = cleaned[-1]
    expanded.append((li, st, min(song_dur - 0.05, st + 10.0), tx))

    return expanded


# ------------------ CSV + JSON ------------------

def write_csv(path: Path, rows: List[Tuple[int, float, float, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for r in rows:
            w.writerow([r[0], f"{r[1]:.3f}", f"{r[2]:.3f}", r[3]])


def write_debug(path: Path, slug: str, word_segments, aligned):
    payload = {
        "slug": slug,
        "version": "3_auto_timing_minimalist",
        "word_segments": word_segments,
        "aligned": [
            {"line_index": li, "start": st, "end": en, "text": tx}
            for (li, st, en, tx) in aligned
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ------------------ MAIN ------------------

def run(slug: str, debug: bool = False):
    log("MAIN", f"=== AUTO-TIMING START for slug={slug} ===", BOLD + CYAN)

    txt = TXT_DIR / f"{slug}.txt"
    if not txt.exists():
        log("ERR", f"Missing {txt}", RED)
        sys.exit(1)

    audio = pick_audio(slug)
    lines = read_lyrics(txt)
    log("LYR", f"{len(lines)} lines", GREEN)

    wx = run_whisperx(audio)
    word_segments = wx["word_segments"]

    if not word_segments:
        log("ERR", "No word segments from WhisperX", RED)
        sys.exit(1)

    tokens, token_times = extract_tokens(word_segments)
    aligned = align_lines(lines, tokens, token_times)

    dur = audio_duration(audio)
    aligned = sanitize(aligned, dur)

    out_csv = TIMINGS_DIR / f"{slug}.csv"
    write_csv(out_csv, aligned)
    log("OUT", f"CSV written → {out_csv}", GREEN)

    if debug:
        dbg = META_DIR / f"{slug}_wx_debug.json"
        write_debug(dbg, slug, word_segments, aligned)
        log("OUT", f"Debug JSON → {dbg}", CYAN)

    log("MAIN", f"=== DONE for slug={slug} ===", GREEN + BOLD)


def main():
    p = argparse.ArgumentParser(description="Minimalist WhisperX Auto-Timing")
    p.add_argument("--slug", required=True)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args()
    run(args.slug, debug=args.debug)


if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
