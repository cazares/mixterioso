#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
timings_to_json_nolyrics.py — keep only segment start/end times
"""
import json, sys
from pathlib import Path

if len(sys.argv) < 2:
    print("Usage: python3 timings_to_json_nolyrics.py <whisper_output.json>")
    sys.exit(1)

in_path = Path(sys.argv[1])
out_path = in_path.with_name(in_path.stem + "_onlytiming.json")

data = json.loads(in_path.read_text())
segments = [
    {"text": None, "start": s.get("start"), "end": s.get("end")}
    for s in data.get("segments", [])
]
out_path.write_text(json.dumps({"segments": segments}, indent=2))
print(f"✅ wrote timing-only JSON → {out_path}")
# end of timings_to_json_nolyrics.py
