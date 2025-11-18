#!/usr/bin/env python3
# scripts/_whisperx_align_driver.py
#
# Standalone WhisperX alignment driver.
# Produces CSV schema:
#   line_index,start,end,text

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

def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}", flush=True)

def read_lyrics(path: Path):
    lines = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        t = raw.strip()
        if t:
            lines.append((i, t))
    return lines

def normalize_token(t: str) -> str:
    return "".join(ch.lower() for ch in t if ch.isalnum())

def group_words_to_lines(lyrics, word_segments):
    """
    Monotone greedy alignment: map word timings → lyric lines.
    Returns list[(line_index, start, end, text)].
    """
    results = []
    wi = 0
    W = len(word_segments)

    norm = []
    for w in word_segments:
        text = w.get("word") or ""
        norm.append((float(w["start"]), float(w["end"]), normalize_token(text)))

    for li, text in lyrics:
        tokens = [normalize_token(t) for t in text.split() if normalize_token(t)]
        if not tokens:
            results.append((li, 0.0, 0.01, text))
            continue

        start = None
        end = None
        ti = 0

        while wi < W and ti < len(tokens):
            w_s, w_e, w_norm = norm[wi]
            t_norm = tokens[ti]
            if t_norm in w_norm or w_norm in t_norm:
                if start is None:
                    start = w_s
                end = w_e
                ti += 1
                wi += 1
            else:
                wi += 1

        if start is None:
            start = 0.0
            end = 0.01
        elif end < start:
            end = start + 0.01

        results.append((li, float(start), float(end), text))

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--lyrics", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    section = "WXDriver"

    audio = Path(args.audio)
    lyrics_path = Path(args.lyrics)
    out_csv = Path(args.output)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    if not audio.exists():
        log(section, f"ERR missing audio: {audio}", RED); sys.exit(1)
    if not lyrics_path.exists():
        log(section, f"ERR missing lyrics: {lyrics_path}", RED); sys.exit(1)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log(section, f"Using device={device}", YELLOW)

    # 1. Load ASR model
    log(section, "Loading WhisperX ASR (distil-large-v3)...", CYAN)
    try:
        model = whisperx.load_model(
            "distil-large-v3", 
            device, 
            compute_type="float32"
        )
    except Exception as e:
        log(section, f"ASR load error: {e}", RED); sys.exit(1)

    # 2. Transcribe
    log(section, f"Transcribing {audio} ...", CYAN)
    try:
        asr = model.transcribe(
            str(audio), 
            language=args.language,
            vad_filter=None
        )
    except Exception as e:
        log(section, f"ASR error: {e}", RED); sys.exit(1)

    # 3. Alignment model
    log(section, "Loading alignment model...", CYAN)
    try:
        align_model, metadata = whisperx.load_align_model(
            args.language, 
            device=device
        )
    except Exception as e:
        log(section, f"Align load err: {e}", RED); sys.exit(1)

    # 4. Align
    log(section, "Running alignment...", CYAN)
    try:
        aligned = whisperx.align(
            asr["segments"],
            align_model,
            metadata,
            str(audio),
            device=device,
            return_char_alignments=False,
        )
    except Exception as e:
        log(section, f"Alignment error: {e}", RED); sys.exit(1)

    words = aligned.get("word_segments") or []
    if not words:
        log(section, "ERR: no word_segments", RED); sys.exit(1)

    # 5. Line mapping
    lyrics = read_lyrics(lyrics_path)
    rows = group_words_to_lines(lyrics, words)

    # 6. Write CSV
    log(section, f"Writing CSV → {out_csv}", GREEN)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start", "end", "text"])
        for li, st, en, text in rows:
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", text])

    log(section, "DONE", GREEN)
    sys.exit(0)

if __name__ == "__main__":
    main()

# end of _whisperx_align_driver.py
