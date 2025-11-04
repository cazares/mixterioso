#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_to_json_nulltiming.py — build a JSON skeleton with lyric lines only
"""
import json, sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python3 lyrics_to_json_nulltiming.py <lyrics.txt>")
    sys.exit(1)

txt_path = Path(sys.argv[1])
out_path = txt_path.with_suffix(".json")

lines = [l.strip() for l in txt_path.read_text().splitlines() if l.strip()]
segments = [{"text": line, "start": None, "end": None} for line in lines]

out_path.write_text(json.dumps({"segments": segments}, indent=2, ensure_ascii=False))
print(f"✅ wrote lyric skeleton → {out_path}")
# end of lyrics_to_json_nulltiming.py
