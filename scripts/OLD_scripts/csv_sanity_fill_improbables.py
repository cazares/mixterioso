#!/usr/bin/env python3
"""
csv_sanity_fill_improbables.py

Generic fixer for "start is right, next comes in too early".

Run right after align and before render.
"""

import argparse
import csv
import statistics
from pathlib import Path
from typing import List, Dict, Any


def read_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            line = (row.get("line") or "").strip()
            if not line:
                continue
            start_raw = (row.get("start") or "").strip()
            try:
                start = float(start_raw)
            except ValueError:
                continue
            end_raw = (row.get("end") or "").strip()
            end_val = float(end_raw) if end_raw else None
            rows.append({"line": line, "start": start, "end": end_val})
    rows.sort(key=lambda x: x["start"])
    return rows


def write_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = ["line", "start", "end"]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                "line": r["line"],
                "start": f"{r['start']:.2f}",
                "end": f"{r['end']:.2f}" if r.get("end") is not None else "",
            })


def word_count(text: str) -> int:
    return len([w for w in text.strip().split() if w])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="CSV to fix IN PLACE")
    ap.add_argument("--min-line-dur", type=float, default=1.4,
                    help="minimum duration we allow for a short line (sec)")
    ap.add_argument("--fast-factor", type=float, default=0.6,
                    help="line is 'too fast' if spw < median * fast-factor")
    ap.add_argument("--min-gap-next", type=float, default=0.05,
                    help="keep at least this much gap before next-next line")
    args = ap.parse_args()

    path = Path(args.csv)
    rows = read_rows(path)
    if len(rows) < 3:
        print("[sanity] not enough rows, skipping")
        return

    # 1) learn median seconds-per-word from overall song
    spw_samples = []
    for i in range(len(rows) - 1):
        line_i = rows[i]["line"]
        if "//by//" in line_i:
            continue
        wc = word_count(line_i)
        if wc == 0:
            continue
        start_i = rows[i]["start"]
        start_next = rows[i + 1]["start"]
        dur = start_next - start_i
        if dur <= 0:
            continue
        if 0.4 <= dur <= 12.0:
            spw_samples.append(dur / wc)

    if spw_samples:
        spw_med = statistics.median(spw_samples)
    else:
        spw_med = 0.6  # fallback
    print(f"[sanity] learned median seconds-per-word ≈ {spw_med:.2f}")

    changed = False

    # 2) detect too-fast line i → push line i+1 forward
    for i in range(len(rows) - 1):
        line_i = rows[i]["line"]
        if "//by//" in line_i:
            continue
        wc = word_count(line_i)
        if wc < 2:
            continue

        start_i = rows[i]["start"]
        start_next = rows[i + 1]["start"]
        dur_i = start_next - start_i
        if dur_i <= 0:
            continue

        spw_i = dur_i / wc

        if spw_i < spw_med * args.fast_factor:
            desired_dur = max(spw_med * wc, args.min_line_dur)
            proposed_next_start = start_i + desired_dur

            if i + 2 < len(rows):
                start_nextnext = rows[i + 2]["start"]
                max_allowed = start_nextnext - args.min_gap_next
                if proposed_next_start > max_allowed:
                    proposed_next_start = max_allowed

            if proposed_next_start > start_next + 1e-3:
                print(
                    f"[sanity:push-next] line {i} '{line_i}' "
                    f"dur={dur_i:.2f}s (spw={spw_i:.2f}) too fast; "
                    f"pushing next from {start_next:.2f} -> {proposed_next_start:.2f}"
                )
                rows[i + 1]["start"] = proposed_next_start
                changed = True

    if changed:
        write_rows(path, rows)
    else:
        print("[sanity] no improbable lines found")


if __name__ == "__main__":
    main()
# end of csv_sanity_fill_improbables.py
