#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_lines_from_words.py
Align lyric lines to word-level Whisper timestamps (auto-detects JSON layout).
Compatible with:
  - whisper_timestamped / whisperX JSON (segments[*].words[*])
  - faster_whisper (flat list of words)
  - plain lists of dicts with start/end/text fields
Outputs CSV with "line,start" at millisecond precision.
"""

import json, csv, logging, sys
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

# ----------------------------- helpers ----------------------------- #

def extract_words(data):
    """Return flat list of {text,start,end} dicts from any known whisper format."""
    words = []

    # Case 1: dict with segments[]
    if isinstance(data, dict):
        if "segments" in data:
            for seg in data["segments"]:
                for w in seg.get("words", []):
                    if all(k in w for k in ("start", "end")):
                        words.append({
                            "text": w.get("text", w.get("word", "")).strip(),
                            "start": round(float(w["start"]), 3),
                            "end": round(float(w["end"]), 3),
                        })
        elif "results" in data:  # alternate nesting
            for seg in data["results"]:
                for w in seg.get("words", []):
                    if all(k in w for k in ("start", "end")):
                        words.append({
                            "text": w.get("text", w.get("word", "")).strip(),
                            "start": round(float(w["start"]), 3),
                            "end": round(float(w["end"]), 3),
                        })
    # Case 2: top-level list of word dicts
    elif isinstance(data, list):
        for w in data:
            if isinstance(w, dict) and all(k in w for k in ("start", "end")):
                words.append({
                    "text": w.get("text", w.get("word", "")).strip(),
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                })
    return words

# ----------------------------- main logic ----------------------------- #

def align_lines(json_path: Path, txt_path: Path, output_csv: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    words = extract_words(data)

    if not words:
        logging.error("💀 No usable word-level timing data found.")
        # debug snapshot
        if isinstance(data, dict):
            logging.error(f"Top-level keys: {list(data.keys())[:10]}")
            first_key = next(iter(data), None)
            logging.error(f"First item under first key: {str(data.get(first_key))[:400]}")
        elif isinstance(data, list):
            logging.error(f"Top-level is list with {len(data)} elements.")
            if data:
                logging.error(f"First element sample: {str(data[0])[:400]}")
        sys.exit(1)

    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        logging.error("❌ Lyrics TXT is empty.")
        sys.exit(1)

    logging.info(f"🎧 Aligning {len(lines)} lyric lines using {len(words)} words...")

    # fuzzy: use start of first matching word (and optionally 2nd word) for each line
    mapped = []
    lw_total = len(lines)
    w_total = len(words)

    for idx, line in enumerate(lines):
        line_words = [w.lower() for w in line.split()]
        if not line_words:
            continue

        # search using first 1–2 words
        first1, first2 = line_words[0][:3], line_words[1][:3] if len(line_words) > 1 else None
        start_time = None

        for i in range(w_total - 1):
            w1 = words[i]["text"].lower().strip()
            w2 = words[i + 1]["text"].lower().strip() if i + 1 < w_total else ""
            if w1.startswith(first1):
                if not first2 or w2.startswith(first2):
                    start_time = words[i]["start"]
                    break

        # fallback: try fuzzy last words match if still none
        if start_time is None:
            last1 = line_words[-1][:3]
            for w in reversed(words):
                if w["text"].lower().startswith(last1):
                    start_time = w["start"]
                    break

        mapped.append([line, f"{start_time if start_time else 0.000:.3f}"])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "start"])
        w.writerows(mapped)

    logging.info(f"✅ Alignment complete → {output_csv} ({len(mapped)} lines)")

# ----------------------------- CLI ----------------------------- #

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Align lyric lines using Whisper word-level timestamps (auto-detect format).")
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    align_lines(Path(args.json), Path(args.text), Path(args.output))

if __name__ == "__main__":
    main()

# end of align_lines_from_words.py
