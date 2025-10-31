#!/usr/bin/env python3
"""
csv_sanity_fill_improbables.py

2-pass fixer for early-song glitches:
- PASS 1: stretch lines that are too short for their words
- PASS 2: push lines that still start too early (or are duplicates nearby)

Keeps all extra CSV columns.
"""

import argparse
import csv
import statistics
from typing import List, Dict, Any


def read_rows(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = [dict(x) for x in r]
        fieldnames = r.fieldnames
    if not fieldnames:
        raise SystemExit("CSV has no header")
    def _s(row):
      try:
        return float(row.get("start", "0") or 0.0)
      except ValueError:
        return 0.0
    rows.sort(key=_s)
    return rows, fieldnames


def write_rows(path: str, fieldnames: List[str], rows: List[Dict[str, Any]]):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def words_in(text: str) -> int:
    return len((text or "").strip().split())


def infer_end(rows: List[Dict[str, Any]], idx: int) -> float:
    this_row = rows[idx]
    start = float(this_row.get("start", "0") or 0.0)
    end_raw = (this_row.get("end") or "").strip()
    if end_raw:
        try:
            return float(end_raw)
        except ValueError:
            pass
    if idx + 1 < len(rows):
        nxt = rows[idx + 1]
        try:
            ns = float(nxt.get("start", "0") or 0.0)
            if ns > start:
                return ns
        except ValueError:
            pass
    return start + 2.0


def compute_global_pw(rows: List[Dict[str, Any]]) -> float:
    vals = []
    for i, r in enumerate(rows):
        txt = r.get("line", "") or ""
        wc = words_in(txt)
        if wc == 0:
            continue
        s = float(r.get("start", "0") or 0.0)
        e = infer_end(rows, i)
        dur = max(0.01, e - s)
        if dur > 25.0:
            continue
        vals.append(dur / wc)
    if not vals:
        return 0.55
    med = statistics.median(vals)
    if med < 0.25:
        med = 0.25
    if med > 1.50:
        med = 1.50
    return med


def is_recent_duplicate(rows: List[Dict[str, Any]], idx: int, window_sec: float = 12.0) -> bool:
    this_txt = (rows[idx].get("line", "") or "").strip().lower()
    this_start = float(rows[idx].get("start", "0") or 0.0)
    if not this_txt:
        return False
    j = idx - 1
    while j >= 0:
        prev_txt = (rows[j].get("line", "") or "").strip().lower()
        prev_start = float(rows[j].get("start", "0") or 0.0)
        if this_start - prev_start > window_sec:
            break
        if prev_txt and prev_txt == this_txt:
            return True
        j -= 1
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)

    # PASS 1: short line stretcher
    ap.add_argument("--min_line_dur", type=float, default=1.25,
                    help="if a line is shorter than this and has space, extend it")
    ap.add_argument("--min_gap_before_next", type=float, default=0.08,
                    help="after extending, still leave this before next line")

    # PASS 2: early/dup pusher
    ap.add_argument("--min_gap_after_prev", type=float, default=0.35,
                    help="when pushing, keep at least this after prev")
    ap.add_argument("--extra_gap_tolerance", type=float, default=0.75,
                    help="if gap to next > expected_dur + this → too early")
    ap.add_argument("--next_guard", type=float, default=0.05,
                    help="never let a line end closer than this to next start")

    args = ap.parse_args()

    rows, fieldnames = read_rows(args.csv)
    if not rows:
        print("[sanity] empty csv")
        return

    global_pw = compute_global_pw(rows)
    changed = 0

    # ---------------- PASS 1: extend too-short lines ----------------
    for i in range(len(rows) - 1):  # last line can't extend to next
        cur = rows[i]
        nxt = rows[i + 1]

        txt = cur.get("line", "") or ""
        wc = words_in(txt)
        if wc == 0:
            continue

        start = float(cur.get("start", "0") or 0.0)
        end = infer_end(rows, i)
        next_start = float(nxt.get("start", "0") or 0.0)

        cur_dur = max(0.01, end - start)
        expected_dur = max(args.min_line_dur, wc * global_pw)

        available = next_start - start - args.min_gap_before_next
        if available < expected_dur:
            continue

        if cur_dur + 0.05 < expected_dur:
            new_end = start + expected_dur
            if new_end > next_start - args.min_gap_before_next:
                new_end = next_start - args.min_gap_before_next
            cur["end"] = f"{new_end:.2f}"
            print(f"[sanity:pass1] extended short line {i}: '{txt[:40]}' "
                  f"{end:.2f} -> {new_end:.2f} (expected {expected_dur:.2f})")
            changed += 1

    # ---------------- PASS 2: push too-early / duplicate lines ------
    for i in range(1, len(rows) - 1):  # need prev and next
        prev = rows[i - 1]
        cur = rows[i]
        nxt = rows[i + 1]

        txt = cur.get("line", "") or ""
        wc = words_in(txt)
        if wc == 0:
            continue

        prev_end = infer_end(rows, i - 1)
        this_start = float(cur.get("start", "0") or 0.0)
        this_end = infer_end(rows, i)
        next_start = float(nxt.get("start", "0") or 0.0)

        expected_dur = max(args.min_line_dur, wc * global_pw)
        gap_to_next = next_start - this_start
        expected_room = expected_dur + args.extra_gap_tolerance

        is_dup = is_recent_duplicate(rows, i, window_sec=12.0)
        too_early = gap_to_next > expected_room

        if not too_early and not is_dup:
            continue

        earliest_allowed = prev_end + args.min_gap_after_prev
        latest_allowed = next_start - expected_dur
        if latest_allowed < earliest_allowed:
            new_start = earliest_allowed
        else:
            if is_dup:
                new_start = earliest_allowed + 0.7 * (latest_allowed - earliest_allowed)
            else:
                new_start = (earliest_allowed + latest_allowed) / 2.0

        new_end = new_start + expected_dur
        limit = next_start - args.next_guard
        if new_end > limit:
            new_end = limit
            if new_end < new_start + 0.10:
                new_end = new_start + 0.10

        if abs(new_start - this_start) >= 0.20:
            print(f"[sanity:pass2] moved early/dup line {i}: '{txt[:40]}' "
                  f"{this_start:.2f} -> {new_start:.2f} (dup={is_dup})")
            cur["start"] = f"{new_start:.2f}"
            cur["end"] = f"{new_end:.2f}"
            changed += 1

    if changed:
        write_rows(args.csv, fieldnames, rows)
        print(f"[sanity] wrote corrected csv → {args.csv} (changed {changed} line(s))")
    else:
        print("[sanity] no changes needed; csv unchanged")


if __name__ == "__main__":
    main()
# end of csv_sanity_fill_improbables.py
