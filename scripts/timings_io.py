#!/usr/bin/env python3
"""
timings_io.py — shared helpers for reading/writing timing CSVs.

Canonical CSV schema (4 columns):

    line_index,start,end,text

- line_index: int, 0-based
- start: float seconds (phrase start)
- end:   float seconds (phrase end)
- text:  lyric line (may contain commas if properly CSV-quoted)

Only 4-column CSV is supported now. Older 3-column files must be regenerated.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, List, Tuple

TimingRow = Tuple[int, float, float, str]


def load_timings_any(path: str | Path) -> List[TimingRow]:
    """
    Load canonical timing CSV and return a list of
        (line_index, start_secs, end_secs, text)
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Timing CSV not found: {p}")

    rows: List[TimingRow] = []

    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)

        header = next(reader, None)
        if header is None:
            return []

        header_norm = [h.strip().lower() for h in header]
        if len(header_norm) < 4:
            raise ValueError(
                f"Expected 4-column CSV with header like "
                f"'line_index,start,end,text', got: {header}"
            )

        # Allow small naming variations for start/end.
        if header_norm[0] != "line_index":
            raise ValueError(
                f"First column must be 'line_index', got: {header[0]!r}"
            )

        start_ok = header_norm[1] in ("start", "start_secs")
        end_ok = header_norm[2] in ("end", "end_secs")
        if not (start_ok and end_ok):
            raise ValueError(
                "Expected header columns: line_index,start,end,text "
                f"(or start_secs/end_secs). Got: {header}"
            )

        for row_idx, row in enumerate(reader, start=2):
            # Skip blank lines
            if not row or all(not cell.strip() for cell in row):
                continue

            # Allow comment-style lines if first cell starts with '#'
            if row[0].lstrip().startswith("#"):
                continue

            if len(row) < 4:
                raise ValueError(
                    f"{p}: row {row_idx} has {len(row)} columns, expected at least 4."
                )

            try:
                line_index = int(row[0].strip())
            except ValueError:
                raise ValueError(
                    f"{p}: row {row_idx} has invalid line_index={row[0]!r}"
                ) from None

            try:
                start = float(row[1].strip())
                end = float(row[2].strip())
            except ValueError:
                raise ValueError(
                    f"{p}: row {row_idx} has invalid start/end seconds: "
                    f"{row[1]!r}, {row[2]!r}"
                ) from None

            # Text may contain commas; if CSV is correctly quoted, it will be in row[3].
            # If we somehow get extra columns, join them back for robustness.
            text = row[3]
            if len(row) > 4:
                text = ",".join(row[3:])

            rows.append((line_index, start, end, text))

    return rows


def save_timings_csv(path: str | Path, rows: Iterable[TimingRow]) -> None:
    """
    Save timing rows to CSV using the canonical 4-column schema:

        line_index,start,end,text
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["line_index", "start", "end", "text"])
        for line_index, start, end, text in rows:
            writer.writerow(
                [line_index, f"{start:.3f}", f"{end:.3f}", text]
            )


# end of timings_io.py
