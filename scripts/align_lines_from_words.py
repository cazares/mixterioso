#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_lines_from_words.py — precise word-level lyric alignment (no gaps, no padding)
Drops 'end' column for Karaoke-Time compatibility.
"""

import csv, json, logging, sys
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

def align_lines(json_path: Path, txt_path: Path, output_csv: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segs = data.get("segments") if isinstance(data, dict) else data
    if not segs:
        logging.error("💀 No segments found in JSON.")
        sys.exit(1)

    # flatten all words into one list
    words = []
    for s in segs:
        for w in s.get("words", []):
            if "start" in w and "end" in w:
                words.append(w)
    if not words:
        logging.error("💀 No word-level timing data found in JSON.")
        sys.exit(1)

    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        logging.error("❌ Lyrics TXT is empty.")
        sys.exit(1)

    logging.info(f"🎧 Aligning {len(lines)} lines using {len(words)} words (no padding)...")

    mapped = []
    word_index = 0
    n_words = len(words)

    for line in lines:
        line_words = line.lower().split()
        match_window = words[word_index : word_index + 50]
        # find best approximate start in next ~50 words
        best_idx = word_index
        for k in range(len(match_window)):
            if match_window[k]["word"].lower().startswith(line_words[0][:3]):
                best_idx = word_index + k
                break
        start_time = float(words[best_idx]["start"])
        mapped.append([line, f"{start_time:.6f}"])
        word_index = min(best_idx + len(line_words), n_words - 1)

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["line", "start"])
        writer.writerows(mapped)

    logging.info(
        f"✅ Alignment complete:\n"
        f"  • {len(lines)} lyric lines\n"
        f"  • {len(words)} total words\n"
        f"  → {output_csv.name} (no silence, ms precision)"
    )

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Align lyric lines using word-level Whisper JSON (no gaps).")
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    align_lines(Path(args.json), Path(args.text), Path(args.output))

if __name__ == "__main__":
    main()

# end of align_lines_from_words.py
