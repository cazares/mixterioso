#!/usr/bin/env python3
# scripts/3_auto_timing.py
#
# MINIMALIST AUTO-TIMING ENGINE (WHISPERX)
#
# Purpose:
#   - Take txts/<slug>.txt and audio for <slug>
#   - Run WhisperX ASR + forced alignment
#   - Align lyric lines using simple, monotone logic
#   - Produce stable CSV timings:
#         line_index,start,end,text
#
# Notes:
#   - Uses Demucs vocal stem when available
#   - Caps each line's on-screen duration to avoid spanning instrumentals
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

# ------------------ Small helpers ------------------


def norm_tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def read_lyrics(path: Path) -> List[str]:
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def pick_audio_for_timing(slug: str) -> Path:
    """
    Prefer a clean vocal stem for ASR, then fall back to full mix.
    """
    candidates = [
        MIXES_DIR / f"{slug}_vocals.wav",   # best (Demucs vocal stem)
        MP3_DIR / f"{slug}.mp3",            # original track
        WAV_DIR / f"{slug}.wav",            # wav version if present
        MIXES_DIR / f"{slug}.wav",          # generic mix
        MIXES_DIR / f"{slug}_karaoke.wav",  # last resort (instrumental)
    ]
    for c in candidates:
        if c.exists():
            log("AUDIO", f"Using audio for timing: {c}", GREEN)
            return c

    log("AUDIO", f"No audio found for slug={slug}", RED)
    sys.exit(1)


def audio_duration_seconds(path: Path) -> float:
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=None, mono=True)
        if sr <= 0:
            return 0.0
        return len(y) / float(sr)
    except Exception as e:
        log("DUR", f"Duration fallback (librosa error: {e})", YELLOW)
        return 0.0


# ------------------ WhisperX engine ------------------


def run_whisperx_on_audio(audio_path: Path, lang: str = "en") -> Dict[str, Any]:
    """
    Run WhisperX ASR + alignment and return:
        {
          "segments": [...],
          "word_segments": [...]
        }
    """
    log("WX", "Loading WhisperX model (medium)…", CYAN)

    import torch  # type: ignore
    import whisperx  # type: ignore

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    model = whisperx.load_model("medium", device=device, compute_type=compute_type)
    log("WX", f"Transcribing {audio_path}", CYAN)
    result = model.transcribe(str(audio_path), batch_size=16)

    log("WX", "Loading alignment model (wav2vec2)…", CYAN)
    model_a, metadata = whisperx.load_align_model(
        language_code=result.get("language", lang),
        device=device,
    )

    log("WX", "Running forced alignment (word-level)…", CYAN)
    aligned = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        str(audio_path),
        device=device,
    )

    word_segments = aligned.get("word_segments") or []
    log("WX", f"Word segments: {len(word_segments)}", GREEN)

    # preview first few
    for i, w in enumerate(word_segments[:10]):
        log(
            "WX",
            f"  WORD[{i}] '{w.get('word','')}' {w.get('start',0):.3f}-{w.get('end',0):.3f}",
            WHITE,
        )

    return {"segments": result.get("segments", []), "word_segments": word_segments}


# ------------------ Token extraction ------------------


def tokens_from_word_segments(word_segments: List[Dict[str, Any]]) -> Tuple[List[str], List[float]]:
    tokens: List[str] = []
    times: List[float] = []

    for w in word_segments:
        text = str(w.get("word", "") or "")
        start = float(w.get("start", 0.0) or 0.0)
        for t in norm_tokens(text):
            tokens.append(t)
            times.append(start)

    return tokens, times


# ------------------ Alignment logic ------------------


def align_lines_to_tokens(
    lines: List[str],
    tokens: List[str],
    token_times: List[float],
    search_pad: int = 40,
    fallback_gap: float = 1.0,
) -> List[Tuple[int, float, float, str]]:
    """
    Very simple, monotone sequence alignment:
      - For each lyric line, try to find its token sequence in the ASR tokens
        within a small window ahead of 'cursor'.
      - If found, start = time of first matched token.
      - Otherwise, place the line fallback_gap seconds after previous line.
    """
    log("ALIGN", "Aligning lyric lines (sequence match)…", CYAN)

    if not tokens or not token_times:
        # Hard fallback: uniform spacing
        t = 0.0
        out: List[Tuple[int, float, float, str]] = []
        for i, line in enumerate(lines):
            out.append((i, t, t + 0.01, line))
            t += fallback_gap
        return out

    N = len(tokens)
    out: List[Tuple[int, float, float, str]] = []
    cursor = 0

    for idx, line in enumerate(lines):
        ltoks = norm_tokens(line)
        if not ltoks:
            base = out[-1][1] if out else token_times[0]
            out.append((idx, base, base + 0.01, line))
            continue

        best_j = None
        j_end = min(N - len(ltoks), cursor + search_pad)

        for j in range(cursor, max(cursor, j_end) + 1):
            if tokens[j: j + len(ltoks)] == ltoks:
                best_j = j
                break

        if best_j is not None:
            st = token_times[best_j]
            out.append((idx, st, st + 0.01, line))
            cursor = best_j + len(ltoks)
        else:
            base = out[-1][1] + fallback_gap if out else token_times[0]
            out.append((idx, base, base + 0.01, line))
            cursor = min(N - 1, cursor + 1)

    return out


# ------------------ Timing sanitizer ------------------


def sanitize_timings(
    rows: List[Tuple[int, float, float, str]],
    song_duration: float,
    max_line_duration: float = 4.0,
) -> List[Tuple[int, float, float, str]]:
    """
    - Ensure starts are monotone and non-negative.
    - Clamp ends to song_duration.
    - Extend each line toward the next line, but never longer than max_line_duration.
    """
    if not rows:
        return rows

    # 1) Sort by start time
    rows_sorted = sorted(rows, key=lambda r: r[1])

    # 2) Enforce monotone starts and clamp within [0, song_duration)
    clean: List[Tuple[int, float, float, str]] = []
    prev_start = 0.0

    for li, st, en, tx in rows_sorted:
        st = max(0.0, st)
        if clean and st <= prev_start:
            st = prev_start + 0.001
        if song_duration > 0:
            st = min(st, max(0.0, song_duration - 0.5))
        en = min(en, song_duration - 0.05) if song_duration > 0 else en
        clean.append((li, st, en, tx))
        prev_start = st

    # 3) Expand toward next start, capped by max_line_duration
    EPS = 0.10
    final_rows: List[Tuple[int, float, float, str]] = []

    for i, (li, st, en, tx) in enumerate(clean):
        if i < len(clean) - 1:
            next_st = clean[i + 1][1]
            target_end = next_st - EPS
        else:
            # last line
            target_end = song_duration - 0.10 if song_duration > 0 else st + max_line_duration

        # Cap by max_line_duration
        target_end = min(target_end, st + max_line_duration)

        if target_end <= st:
            target_end = st + 0.01

        final_rows.append((li, st, target_end, tx))

    # Sort back by line_index for readability
    final_rows.sort(key=lambda r: r[0])
    return final_rows


# ------------------ CSV / debug writers ------------------


def write_canonical_csv(path: Path, rows: List[Tuple[int, float, float, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, st, en, tx in rows:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", tx])


def write_debug_json(
    dbg_path: Path,
    slug: str,
    word_segments: List[Dict[str, Any]],
    aligned_rows: List[Tuple[int, float, float, str]],
) -> None:
    payload = {
        "slug": slug,
        "version": "3_auto_timing_minimalist_v2",
        "word_segments": word_segments,
        "aligned": [
            {"line_index": li, "start": st, "end": en, "text": tx}
            for (li, st, en, tx) in aligned_rows
        ],
    }
    dbg_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ------------------ MAIN PIPELINE ------------------


def run_auto_timing(slug: str, debug: bool = False) -> None:
    log("MAIN", f"=== AUTO-TIMING START for slug={slug} ===", BOLD + CYAN)

    txt_path = TXT_DIR / f"{slug}.txt"
    if not txt_path.exists():
        log("MAIN", f"Missing txt file: {txt_path}", RED)
        sys.exit(1)

    audio_path = pick_audio_for_timing(slug)

    lines = read_lyrics(txt_path)
    log("LYR", f"Loaded {len(lines)} lyric lines", GREEN)

    wx = run_whisperx_on_audio(audio_path)
    word_segments = wx["word_segments"]

    if not word_segments:
        log("WX", "No word segments from WhisperX — aborting.", RED)
        sys.exit(1)

    tokens, token_times = tokens_from_word_segments(word_segments)

    aligned = align_lines_to_tokens(lines, tokens, token_times)

    song_duration = audio_duration_seconds(audio_path)
    log("DUR", f"Song duration ≈ {song_duration:.3f}s", CYAN)

    aligned = sanitize_timings(aligned, song_duration)

    out_csv = TIMINGS_DIR / f"{slug}.csv"
    write_canonical_csv(out_csv, aligned)
    log("OUT", f"WROTE canonical timings CSV: {out_csv}", GREEN)

    if debug:
        dbg_path = META_DIR / f"{slug}_whisperx_debug.json"
        write_debug_json(dbg_path, slug, word_segments, aligned)
        log("OUT", f"WROTE debug JSON: {dbg_path}", CYAN)

    log("MAIN", f"=== AUTO-TIMING DONE for slug={slug} ===", GREEN + BOLD)


# ------------------ CLI ------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Minimalist WhisperX auto-timing for karaoke pipeline")
    p.add_argument("--slug", required=True, help="Song slug, e.g. nirvana_come_as_you_are")
    p.add_argument("--debug", action="store_true", help="Write meta/<slug>_whisperx_debug.json")
    args = p.parse_args()

    run_auto_timing(args.slug, debug=args.debug)


if __name__ == "__main__":
    main()

# end of 3_auto_timing.py
