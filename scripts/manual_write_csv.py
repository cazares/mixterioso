#!/usr/bin/env python3
# scripts/manual_write_csv.py

from __future__ import annotations
from pathlib import Path
import csv


def write_manual_csv(path: Path, lyrics, timings):
    """
    Tests pass timings like:
        {"line_index": int, "time": float, "text": str}

    The test expects:
        end_secs = start_secs + epsilon
        exact header: line_index,start_secs,end_secs,text
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    # sort timings by time
    t_sorted = sorted(
        [t for t in timings if isinstance(t, dict)],
        key=lambda t: t["time"]
    )

    rows = []
    for t in t_sorted:
        line_index = t["line_index"]
        start = float(t["time"])
        end = start + 0.010  # epsilon required by tests
        text = lyrics[line_index] if 0 <= line_index < len(lyrics) else t.get("text", "")
        rows.append((line_index, start, end, text))

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["line_index", "start_secs", "end_secs", "text"])
        for line_index, start, end, text in rows:
            w.writerow([
                line_index,
                f"{start:.3f}",
                f"{end:.3f}",
                text
            ])

# end of manual_write_csv.py
