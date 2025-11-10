#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combine_lyrics_and_timings.py — merge lyric-only and timing-only JSONs
"""
import json, sys
from pathlib import Path

if len(sys.argv) < 4:
    print("Usage: python3 combine_lyrics_and_timings.py <lyrics.json> <timings.json> <output.json>")
    sys.exit(1)

lyrics_json, timing_json, out_json = map(Path, sys.argv[1:4])
lyrics = json.loads(lyrics_json.read_text()).get("segments", [])
timings = json.loads(timing_json.read_text()).get("segments", [])

segments = []
for i, lyr in enumerate(lyrics):
    if i < len(timings):
        seg = {"text": lyr["text"], "start": timings[i]["start"], "end": timings[i]["end"]}
    else:
        seg = {"text": lyr["text"], "start": None, "end": None}
    segments.append(seg)

out_json.write_text(json.dumps({"segments": segments}, indent=2, ensure_ascii=False))
print(f"✅ combined {len(segments)} segments → {out_json}")
# end of combine_lyrics_and_timings.py
