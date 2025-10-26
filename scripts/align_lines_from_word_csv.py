#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_lines_from_word_csv.py
Accurate lyric line alignment from word-level CSV.
- Finds start = first matched word.start
- Finds end   = last matched word.end
- Merges lines with same start
- Outputs sorted by start time (3 decimal precision)
"""

import csv, re, sys, logging
from pathlib import Path
from rapidfuzz import fuzz

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)

def normalize(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", t.lower())

def align_lines(words_csv: Path, txt_path: Path, output_csv: Path):
    # Load word timings
    words = []
    with words_csv.open() as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                start = float(row["start"])
                end = float(row["end"])
                text = row["word"].strip()
                norm = normalize(text)
                if norm:
                    words.append((text, start, end, norm))
            except Exception:
                continue
    if not words:
        logging.error("💀 No words parsed from CSV")
        sys.exit(1)

    # Load lyrics lines
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        logging.error("💀 Lyrics TXT empty")
        sys.exit(1)

    logging.info(f"🎧 Aligning {len(lines)} lines using {len(words)} words")

    aligned = []
    for line in lines:
        line_norm = normalize(line)
        if not line_norm:
            continue

        # Find best matching region by first + last word fuzz
        best_start, best_end, best_score = None, None, -1
        for i in range(len(words)):
            j = min(i + 10, len(words))
            chunk = "".join(w[3] for w in words[i:j])
            score = fuzz.partial_ratio(line_norm, chunk)
            if score > best_score:
                best_score = score
                best_start = words[i][1]
                best_end = words[j - 1][2]

        if best_start is not None:
            aligned.append((line, best_start, best_end, best_score))

    # Sort and merge duplicates
    aligned.sort(key=lambda x: x[1])
    merged = []
    for line, start, end, score in aligned:
        if merged and abs(start - merged[-1][1]) < 0.001:
            merged[-1] = (merged[-1][0] + r"\N" + line, merged[-1][1], end, score)
        else:
            merged.append((line, start, end, score))

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line", "start"])
        for line, start, *_ in merged:
            w.writerow([line, f"{start:.3f}"])

    logging.info(f"✅ {len(merged)} aligned lines → {output_csv.name}")

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    align_lines(Path(args.words), Path(args.text), Path(args.output))

if __name__ == "__main__":
    main()
# end of align_lines_from_word_csv.py
