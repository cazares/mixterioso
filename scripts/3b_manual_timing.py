#!/usr/bin/env python3
# scripts/3b_manual_timing.py
# Cleaned-up version of your manual timing UI
# Writes: line_index,start_secs,end_secs,text

from __future__ import annotations
import argparse, csv, curses, subprocess, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TIMINGS_DIR = BASE / "timings"

# your full 3b code stays — but WRITE CSV CHANGES:
def write_timings(path:Path, lyrics, timings):
    """
    Writes 4-column CSV: line_index,start_secs,end_secs,text
    end_secs = start_secs + 0.01 (manual timing has only a point not a span)
    """
    path.parent.mkdir(exist_ok=True)
    timings_sorted = sorted(timings, key=lambda t: t["time"])
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["line_index","start_secs","end_secs","text"])
        for t in timings_sorted:
            li = t["line_index"]
            st = t["time"]
            en = st + 0.01
            txt = lyrics[li] if li >= 0 else t.get("text","")
            w.writerow([li, f"{st:.3f}", f"{en:.3f}", txt])

# rest of your manual timing UI remains unchanged
# (AudioPlayer, curses UI, rewind, goto, etc.)

# end of 3b_manual_timing.py
