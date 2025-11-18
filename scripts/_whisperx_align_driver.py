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
#   - Run WhisperX ASR on the audio file (distil-large-v3)
#   - Run WhisperX alignment model
#   - Map word-level timings to lyric lines (monotone, best-effort)
#   - Emit canonical CSV: line_index,start,end,text

import argparse
import csv
import sys
import time
from pathlib import Path

import torch
import whisperx

RESET  = "\033[0m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)


def read_lyrics(path: Path) -> list[tuple[int, str]]:
    """
    Reads TXT lyrics file, returns list of (line_index, text) for non-empty lines.
    """
    lines: list[tuple[int, str]] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = raw.strip()
        if text:
            lines.append((i, text))
    return lines


def normalize_token(token: str) -> str:
    return "".join(ch.lower() for ch in token if ch.isalnum())


def group_words_to_lines(
    lyrics: list[tuple[int, str]],
    word_segments: list[dict],
) -> list[tuple[int, float, float, str]]:
    """
    Monotone greedy alignment:
      - word_segments: [{"word": "...", "start": float, "end": float}, ...]
      - lyrics: [(line_index, text), ...]

    For each lyric line:
      - Tokenize text
      - Walk forward through word_segments, matching tokens in order
      - First match sets start_time, last match sets end_time

    Returns list of (line_index, start, end, text).
    """
    results: list[tuple[int, float, float, str]] = []
    wi = 0
    n_words = len(word_segments)

    # Pre-normalize word text
    norm_words: list[tuple[float, float, str]] = []
    for w in word_segments:
        wtext = w.get("word") or w.get("text") or ""
        norm_words.append(
            (
                float(w.get("start", 0.0)),
                float(w.get("end", 0.0)),
                normalize_token(wtext),
            )
        )

    for line_index, text in lyrics:
        tokens = [normalize_token(t) for t in text.split() if normalize_token(t)]
        if not tokens:
            # Empty/whitespace-only line: give tiny stub duration
            results.append((line_index, 0.0, 0.01, text))
            continue

        start_time: float | None = None
        end_time: float | None = None
        ti = 0  # token index

        while wi < n_words and ti < len(tokens):
            w_start, w_end, w_norm = norm_words[wi]
            t_norm = tokens[ti]

            if not t_norm:
                ti += 1
                continue

            # simple fuzzy match: substring in either direction
            match = (t_norm in w_norm) or (w_norm in t_norm)
            if match:
                if start_time is None:
                    start_time = w_start
                end_time = w_end
                ti += 1
                wi += 1
            else:
                wi += 1

        if start_time is None:
            # No match at all: give stub near 0
            start_time = 0.0
            end_time = 0.01
        elif end_time is None or end_time < start_time:
            end_time = start_time + 0.01

        results.append((line_index, float(start_time), float(end_time), text))

    return results


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

    out_csv.parent.mkdir(parents=True, exist_ok=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(section, f"Using device={device}", YELLOW)

    # 1) Load ASR model (distil-large-v3)
    log(section, "Loading WhisperX ASR model (distil-large-v3)...", CYAN)
    try:
        model = whisperx.load_model("distil-large-v3", device, compute_type="float32")
    except Exception as e:
        log(section, f"ERROR loading ASR model: {e}", RED)
        sys.exit(1)

    # 2) Transcribe (VAD OFF for music)
    log(section, f"Transcribing {audio_path} ...", CYAN)
    try:
        asr_result = model.transcribe(
            str(audio_path),
            language=args.language,
            vad_filter=False,  # IMPORTANT: VAD OFF for songs
        )
    except Exception as e:
        log(section, f"ERROR during transcription: {e}", RED)
        sys.exit(1)

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

    # 5) Read lyrics and align line-level timings
    lyrics = read_lyrics(lyrics_path)
    if not lyrics:
        log(section, "WARNING: lyrics file is empty after stripping; CSV will be empty", YELLOW)

    rows = group_words_to_lines(lyrics, word_segments)

    # 6) Write canonical CSV
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
