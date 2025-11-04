#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_fuzzy_from_json.py — fuzzy-match Whisper transcript to source lyrics.
Generates Karaoke-Time-compatible CSV ("line,start") where each end time
is the next line’s start.
"""

import json, csv, re, sys
from pathlib import Path
from rapidfuzz import fuzz, process

def normalize(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", t.lower())

def fuzzy_align(json_path: Path, txt_path: Path, output_csv: Path):
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    segs = raw["segments"] if isinstance(raw, dict) else raw
    lines = [l.strip() for l in txt_path.read_text(encoding="utf-8").splitlines() if l.strip()]

    norm_lines = [normalize(l) for l in lines]
    norm_segs = [normalize(s.get("text", "")) for s in segs]

    rows = [["line", "start"]]

    for lyric, norm in zip(lines, norm_lines):
        best_match = process.extractOne(norm, norm_segs, scorer=fuzz.partial_ratio)
        if best_match:
            matched_text, score, idx = best_match
            seg = segs[idx]
            start = seg.get("start", 0.0)
        else:
            start = 0.0
        rows.append([lyric, f"{float(start):.3f}"])

    with output_csv.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)

    print(f"✅ Fuzzy-aligned {len(lines)} lyric lines using {len(segs)} Whisper segments → {output_csv.name}")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Fuzzy-align Whisper JSON to lyrics TXT (outputs line,start CSV).")
    ap.add_argument("--json", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    fuzzy_align(Path(args.json).expanduser(), Path(args.text).expanduser(), Path(args.output).expanduser())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)

# end of align_fuzzy_from_json.py
