#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_words_to_csv.py — universal Whisper JSON word extractor.
Tolerates schema variations from whisper_timestamped, whisperX, and openai-whisper.
"""

import json, csv, re, sys
from pathlib import Path

def normalize_word(w: str) -> str:
    w = w.lower().strip()
    w = re.sub(r"[^\w']", "", w)
    w = re.sub(r"'", "", w)
    w = re.sub(r"ing$", "in", w)
    return w

def extract_all_words(obj):
    """Recursively yield dicts with 'word','start','end'."""
    if isinstance(obj, dict):
        if ("start" in obj and "end" in obj and 
            any(k in obj for k in ("word", "text", "token"))):
            text = obj.get("word") or obj.get("text") or obj.get("token")
            if isinstance(text, str) and text.strip():
                yield {"word": text.strip(),
                       "start": float(obj["start"]),
                       "end": float(obj["end"])}
        for v in obj.values():
            yield from extract_all_words(v)
    elif isinstance(obj, list):
        for x in obj:
            yield from extract_all_words(x)

def extract_words(json_path: Path, out_csv: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    words = list(extract_all_words(data))

    if not words:
        sys.exit("💀 No word-level timing data found anywhere in JSON")

    # sort + deduplicate
    seen = set()
    clean_words = []
    for w in sorted(words, key=lambda x: x["start"]):
        key = (w["word"], w["start"], w["end"])
        if key not in seen:
            seen.add(key)
            w["normalized"] = normalize_word(w["word"])
            clean_words.append(w)

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["word","start","end","normalized"])
        w.writeheader()
        for r in clean_words:
            r["start"] = f"{r['start']:.3f}"
            r["end"] = f"{r['end']:.3f}"
            w.writerow(r)

    print(f"✅ Extracted {len(clean_words)} words → {out_csv}")

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Extract all words from Whisper JSON with timing.")
    ap.add_argument("--json", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    extract_words(Path(args.json), Path(args.output))

if __name__ == "__main__":
    main()

# end of extract_words_to_csv.py
