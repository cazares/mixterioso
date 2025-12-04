#!/usr/bin/env python3
# scripts/_whisperx_align_driver.py
#
# Standalone WhisperX ASR + alignment driver.
# Called by 4_merge.py:
#   python3 scripts/_whisperx_align_driver.py \
#       --audio mp3s/<slug>.mp3 \
#       --lyrics txts/<slug>.txt \
#       --output timings/<slug>.csv \
#       --language en
#
# Responsibilities:
#   - Run WhisperX ASR (faster-whisper) on the audio file
#   - Run WhisperX alignment model to get word-level timings
#   - Use a greedy line alignment (ported from mp3_txt_to_timings)
#   - Emit canonical CSV: line_index,start,end,text

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
import re

import torch
import whisperx


RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"

# You can tweak this if needed; distil-large-v3 is a good CPU-friendly choice.
ASR_MODEL = "distil-large-v3"

# ---------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)


# ---------------------------------------------------------------------
# Basic types & tokenization (ported from mp3_txt_to_timings.py)
# ---------------------------------------------------------------------
@dataclass
class Word:
    text: str
    start: float
    end: float


_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def norm_tokens(text: str) -> List[str]:
    """
    Normalize a string into a list of simple alnum tokens.
    """
    return _WORD_RE.findall(text.lower())


def read_lyrics(path: Path) -> List[Tuple[int, str]]:
    """
    Reads TXT lyrics file, returns list of (line_index, text) for non-empty lines.
    line_index is the original 0-based line number in the file.
    """
    out: List[Tuple[int, str]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = raw.strip()
        if text:
            out.append((i, text))
    return out


# ---------------------------------------------------------------------
# Greedy line alignment (adapted from mp3_txt_to_timings) 
# ---------------------------------------------------------------------
def greedy_line_alignment(
    lyrics: List[Tuple[int, str]],
    words: List[Word],
    min_ratio: float = 0.55,
    search_pad: int = 48,
) -> List[Tuple[int, float, str]]:
    """
    lyrics: list of (original_line_index, text)
    words:  list of Word(text,start,end)

    Returns list of (original_line_index, time_secs, text) with:
      - Monotone, non-decreasing times
      - Fallback if we can't find a good window
    """
    if not lyrics or not words:
        return []

    # Flatten word tokens and associated times
    word_tokens: List[str] = []
    word_times: List[float] = []
    for w in words:
        wtoks = norm_tokens(w.text)
        if not wtoks:
            continue
        for _ in wtoks:
            word_tokens.append(wtoks[0])  # we just need some token; norm_tokens already normalized
            word_times.append(float(w.start))

    if not word_tokens:
        return []

    N = len(word_tokens)

    out: List[Tuple[int, float, str]] = []
    cursor = 0

    for orig_index, text in lyrics:
        ltoks = norm_tokens(text)
        if not ltoks:
            # empty-ish line: reuse previous timestamp if possible
            ts = out[-1][1] if out else 0.0
            out.append((orig_index, ts, text))
            continue

        approx_len = max(1, len(ltoks))

        best_ratio = -1.0
        best_j = None
        best_k = None

        j_start = max(0, min(cursor, N - 1))
        j_end = min(N - 1, cursor + search_pad)

        ltoks_set = set(ltoks)

        for j in range(j_start, j_end + 1):
            # Try windows around approx_len
            k_min = min(N, j + approx_len)
            k_max = min(N, j + approx_len + approx_len // 2 + 1)
            for k in range(k_min, k_max):
                window = word_tokens[j:k]
                if not window:
                    continue
                window_set = set(window)
                hits = sum(1 for t in ltoks_set if t in window_set)
                ratio = hits / float(len(ltoks_set))
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_j = j
                    best_k = k

        if best_ratio is not None and best_ratio >= min_ratio and best_j is not None:
            j = best_j
            ts = word_times[j] if j < len(word_times) else (out[-1][1] if out else 0.0)
            out.append((orig_index, ts, text))
            cursor = max(cursor, best_k or (j + 1))
        else:
            # Fallback: move forward in time even if we didn't find a good match.
            prev = out[-1][1] if out else 0.0
            ts = prev + 1.75  # your original-style "just keep the train moving" bump
            out.append((orig_index, ts, text))
            cursor = min(N - 1, cursor + approx_len)

    # Enforce strictly non-decreasing times with tiny epsilon
    fixed: List[Tuple[int, float, str]] = []
    last = -1e9
    eps = 1e-3
    for orig_index, ts, text in out:
        if ts <= last:
            ts = last + eps
        fixed.append((orig_index, ts, text))
        last = ts

    return fixed


# ---------------------------------------------------------------------
# Build start/end from starts
# ---------------------------------------------------------------------
def build_rows_with_ends(
    aligned_triples: List[Tuple[int, float, str]],
    audio_duration: float | None,
    min_visible: float = 2.5,
    max_visible: float = 4.0,
) -> List[Tuple[int, float, float, str]]:
    """
    aligned_triples: [(line_index, start, text), ...] in lyric order.
    Returns: [(line_index, start, end, text), ...]
    """
    if not aligned_triples:
        return []

    rows: List[Tuple[int, float, float, str]] = []
    n = len(aligned_triples)

    for i, (line_index, start, text) in enumerate(aligned_triples):
        if i + 1 < n:
            next_start = aligned_triples[i + 1][1]
            # Try to keep line visible between min_visible and max_visible,
            # but never overlapping the next line.
            ideal_end = start + max_visible
            latest_end = next_start - 0.12  # small safety margin

            if audio_duration is not None:
                latest_end = min(latest_end, audio_duration)

            end = ideal_end
            if latest_end > start:
                end = min(ideal_end, latest_end)
            if end - start < min_visible:
                end = start + min_visible
        else:
            # Last line: cap to audio duration if known.
            if audio_duration is not None and audio_duration > start:
                end = min(start + max_visible, audio_duration)
                if end <= start:
                    end = start + min_visible
            else:
                end = start + max_visible

        if end <= start:
            end = start + min_visible

        rows.append((line_index, start, end, text))

    return rows


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="Path to audio file (mp3/wav)")
    ap.add_argument("--lyrics", required=True, help="Path to lyrics txt file")
    ap.add_argument("--output", required=True, help="Output CSV path")
    ap.add_argument("--language", default="en", help="Language code (e.g. en, es)")
    args = ap.parse_args()

    section = "WXDriver"

    audio_path = Path(args.audio)
    lyrics_path = Path(args.lyrics)
    out_csv = Path(args.output)

    if not audio_path.exists():
        log(section, f"ERROR: audio file not found: {audio_path}", RED)
        sys.exit(1)
    if not lyrics_path.exists():
        log(section, f"ERROR: lyrics file not found: {lyrics_path}", RED)
        sys.exit(1)

    lyrics = read_lyrics(lyrics_path)
    if not lyrics:
        log(section, "ERROR: lyrics file is empty after stripping.", RED)
        sys.exit(1)

    out_csv.parent.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(section, f"Using device={device}, model={ASR_MODEL}", YELLOW)

    # 1) Load ASR model
    log(section, "Loading WhisperX ASR model...", CYAN)
    try:
        model = whisperx.load_model(
            ASR_MODEL,
            device,
            compute_type="float32",
        )
    except Exception as e:
        log(section, f"ERROR loading ASR model: {e}", RED)
        sys.exit(1)

    # 2) Transcribe
    log(section, f"Transcribing {audio_path} ...", CYAN)
    try:
        asr_result = model.transcribe(
            str(audio_path),
            language=args.language,
        )
    except Exception as e:
        log(section, f"ERROR during transcription: {e}", RED)
        sys.exit(1)

    # Estimate audio duration from segments (for nicer end times)
    audio_duration = None
    segments = asr_result.get("segments") or []
    if segments:
        try:
            audio_duration = float(segments[-1].get("end", 0.0))
        except Exception:
            audio_duration = None

    # 3) Load alignment model
    log(section, "Loading alignment model...", CYAN)
    try:
        align_model, metadata = whisperx.load_align_model(
            language_code=args.language,
            device=device,
        )
    except Exception as e:
        log(section, f"ERROR loading align model: {e}", RED)
        sys.exit(1)

    # 4) Run alignment
    log(section, "Running alignment...", CYAN)
    try:
        aligned = whisperx.align(
            asr_result["segments"],
            align_model,
            metadata,
            str(audio_path),
            device=device,
            return_char_alignments=False,
        )
    except Exception as e:
        log(section, f"ERROR during alignment: {e}", RED)
        sys.exit(1)

    word_segments = aligned.get("word_segments") or []
    if not word_segments:
        log(section, "ERROR: no word_segments in alignment result", RED)
        sys.exit(1)

    # Convert to Word list for greedy alignment
    words: List[Word] = []
    for w in word_segments:
        wtext = w.get("word") or w.get("text") or ""
        if not wtext:
            continue
        try:
            wstart = float(w.get("start", 0.0))
            wend = float(w.get("end", wstart + 0.01))
        except Exception:
            continue
        words.append(Word(text=wtext, start=wstart, end=wend))

    if not words:
        log(section, "ERROR: alignment produced no usable words", RED)
        sys.exit(1)

    # 5) Run greedy line alignment (ported logic)
    triples = greedy_line_alignment(lyrics, words)
    if not triples:
        log(section, "ERROR: greedy alignment produced no triples", RED)
        sys.exit(1)

    # 6) Build start/end for each line
    rows = build_rows_with_ends(triples, audio_duration=audio_duration)

    # 7) Write canonical CSV
    log(section, f"Writing CSV → {out_csv}", GREEN)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "start", "end", "text"])
        for line_index, start, end, text in rows:
            writer.writerow([line_index, f"{start:.3f}", f"{end:.3f}", text])

    log(section, "DONE", GREEN)
    sys.exit(0)


if __name__ == "__main__":
    main()

# end of _whisperx_align_driver.py
