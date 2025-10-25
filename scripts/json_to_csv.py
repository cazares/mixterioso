#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
json_to_csv.py — convert WhisperX output JSON into karaoke_time_by_miguel.py CSV
"""

import json, csv, sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python3 json_to_csv.py <input.json>")
    sys.exit(1)

json_path = Path(sys.argv[1])
csv_path = json_path.parent / (json_path.stem + "_timestamps.csv")

data = json.loads(json_path.read_text())
segments = data.get("segments", [])

with csv_path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["line", "start"])
    for seg in segments:
        writer.writerow([seg["text"].strip(), f"{seg['start']:.3f}"])

print(f"✅ Wrote {len(segments)} lines → {csv_path}")
