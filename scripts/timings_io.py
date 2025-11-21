# scripts/timings_io.py
# Utility to read canonical timing CSVs:
#     line_index,start,end,text

import csv
from pathlib import Path

def load_timings_any(csv_path: Path):
    """
    Reads canonical CSV with columns:
        line_index,start,end,text
    Returns list of tuples:
        (line_index, start, end, text)
    """
    rows = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                li = int(row.get("line_index", 0))
                start = float(row.get("start", 0.0))
                end = float(row.get("end", start))
                text = row.get("text", "") or ""
                rows.append((li, start, end, text))
            except Exception:
                # Skip malformed rows
                continue
    return rows
