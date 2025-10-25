#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_lines_from_word_csv.py — sequential alignment using word-level timestamps.
Features:
  • Moves forward through word stream (no backtracking)
  • Sorts by start time
  • Merges only identical timestamps (exact to 3 decimals)
  • Outputs 3-decimal precision
"""

import csv, re, sys
from pathlib import Path
from collections import defaultdict

def normalize(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w']", " ", s)
    s = re.sub(r"'", "", s)
    s = re.sub(r"ing\\b", "in", s)
    s = re.sub(r"\\s+", " ", s)
    return s.strip()

def read_word_csv(path: Path):
    words = []
    with path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                start = float(row["start"])
            except Exception:
                continue
            words.append({
                "word": normalize(row.get("normalized") or row.get("word") or ""),
                "start": start
            })
    return words

def find_line_start(line_words, word_stream, start_index):
    """
    Sequential scan: find first match of line_words after start_index.
    Returns (start_time, next_index)
    """
    n = len(word_stream)
    lw_first = line_words[0]
    lw_second = line_words[1] if len(line_words) > 1 else None

    for i in range(start_index, n):
        w = word_stream[i]["word"]
        if w == lw_first:
            if lw_second and i + 1 < n and word_stream[i + 1]["word"] == lw_second:
                return word_stream[i]["start"], i + len(line_words)
            else:
                return word_stream[i]["start"], i + len(line_words)
    # if no match found, return previous time
    last_time = word_stream[-1]["start"] if word_stream else 0.0
    return last_time, n

def align_lines(word_csv: Path, txt_path: Path, output_csv: Path):
    words = read_word_csv(word_csv)
    if not words:
        sys.exit("💀 No words found in CSV")

    text = txt_path.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        sys.exit("💀 Lyrics TXT is empty")

    raw_rows = []
    prev_time = 0.0
    idx = 0
    for line in lines:
        line_norm = normalize(line)
        line_words = line_norm.split()
        if not line_words:
            continue
        t, idx = find_line_start(line_words, words, idx)
        if t is None:
            t = prev_time
        raw_rows.append((line, round(t, 3)))
        prev_time = t

    # merge identical timestamps only
    merged = defaultdict(list)
    for line, t in raw_rows:
        merged[t].append(line)

    sorted_items = sorted(merged.items(), key=lambda x: x[0])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line","start"])
        for t, lines in sorted_items:
            merged_line = "\\N".join(lines)
            w.writerow([merged_line, f"{t:.3f}"])

    print(f"✅ {len(sorted_items)} unique timestamps → {output_csv}")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Sequentially align lyric lines using per-word CSV timestamps.")
    ap.add_argument("--words", required=True, help="CSV file from extract_words_to_csv.py")
    ap.add_argument("--text", required=True, help="Lyrics .txt file")
    ap.add_argument("--output", required=True, help="Output CSV path")
    args = ap.parse_args()
    align_lines(Path(args.words), Path(args.text), Path(args.output))

if __name__ == "__main__":
    main()

# end of align_lines_from_word_csv.py
