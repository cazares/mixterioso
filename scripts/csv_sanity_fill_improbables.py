#!/usr/bin/env python3
# csv_sanity_fill_improbables.py
#
# Detect lines that start way too early (sec/word too small),
# and snap them FORWARD so that:
#
#   t0_new_start = t1 - (median_spw * words_in_t0)
#   t0_new_end   = t1
#
# …while still respecting the previous line’s end (+ a tiny gap).
#
# We do NOT change the lyric text anymore — we keep the original line.
#
# Usage:
#   python3 scripts/csv_sanity_fill_improbables.py --csv your.csv
#
# Tunables:
#   --factor    how aggressive (default 0.45)
#   --gap       min gap after previous line (default 0.05s)

import argparse
import csv
import statistics
from typing import List, Dict, Any


def _to_f(x: str, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def _compute_end(rows: List[Dict[str, Any]], idx: int) -> float:
    row = rows[idx]
    start = _to_f(row.get("start", "0"), 0.0)
    end_raw = (row.get("end") or "").strip()
    if end_raw:
        return _to_f(end_raw, start + 2.0)
    # estimate from next line
    if idx + 1 < len(rows):
        nxt_start = _to_f(rows[idx + 1].get("start", "0"), start + 2.0)
        return max(start + 0.1, nxt_start)
    return start + 2.0


def read_rows(path: str) -> List[Dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = list(r)
    rows.sort(key=lambda r: _to_f(r.get("start", "0"), 0.0))
    return rows


def write_rows(path: str, rows: List[Dict[str, Any]]):
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--factor", type=float, default=0.45,
                    help="line is 'improbable' if spw < median_spw * factor")
    ap.add_argument("--max-words", type=int, default=18,
                    help="ignore super long lines when computing baseline")
    ap.add_argument("--min-dur", type=float, default=0.35,
                    help="ignore very short lines when computing baseline")
    ap.add_argument("--gap", type=float, default=0.05,
                    help="minimum gap after previous line when shifting")
    args = ap.parse_args()

    rows = read_rows(args.csv)
    if not rows:
        return

    # 1) build baseline sec/word
    spws = []
    for i, row in enumerate(rows):
        text = (row.get("line") or "").strip()
        if not text:
            continue
        words = text.split()
        if not words:
            continue
        if len(words) > args.max_words:
            continue

        start = _to_f(row.get("start", "0"), 0.0)
        end = _compute_end(rows, i)
        dur = max(0.01, end - start)
        if dur < args.min_dur:
            continue

        spw = dur / len(words)
        spws.append(spw)

    if not spws:
      print("[sanity] no baseline could be computed; skipping.")
      return

    median_spw = statistics.median(spws)
    cutoff = median_spw * args.factor
    print(f"[sanity] median spw ≈ {median_spw:.3f}, cutoff ≈ {cutoff:.3f}")

    changed = False

    # 2) scan for improbable lines — must have a NEXT line to anchor to
    for i in range(0, len(rows) - 1):
        row = rows[i]
        next_row = rows[i + 1]

        text = (row.get("line") or "").strip()
        if not text:
            continue
        words = text.split()
        if not words:
            continue

        start0 = _to_f(row.get("start", "0"), 0.0)
        end0 = _compute_end(rows, i)
        dur0 = max(0.01, end0 - start0)
        spw0 = dur0 / len(words)

        if spw0 >= cutoff:
            continue  # looks okay

        # this line is too fast → snap it forward
        t1 = _to_f(next_row.get("start", "0"), 0.0)
        # expected duration for this line
        delta0 = median_spw * len(words)
        # raw target start
        new_start = t1 - delta0

        # honour previous line end
        if i > 0:
            prev_end = _compute_end(rows, i - 1)
            min_start = prev_end + args.gap
            if new_start < min_start:
                new_start = min_start

        # also, don't let it go negative
        if new_start < 0:
            new_start = 0.0

        new_end = t1  # we anchor to next line's start

        row["start"] = f"{new_start:.2f}"
        row["end"] = f"{new_end:.2f}"

        print(
            f"[sanity] line {i} '{text}' was too fast (spw={spw0:.3f} < {cutoff:.3f}) → "
            f"moved to {new_start:.2f}s..{new_end:.2f}s (anchored to next at {t1:.2f}s)"
        )
        changed = True

    if changed:
        write_rows(args.csv, rows)
        print(f"[sanity] wrote sanitized CSV to {args.csv}")
    else:
        print("[sanity] no improbable lines found")


if __name__ == "__main__":
    main()
# end of csv_sanity_fill_improbables.py
