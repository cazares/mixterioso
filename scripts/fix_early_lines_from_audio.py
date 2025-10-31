#!/usr/bin/env python3
"""
fix_early_lines_from_audio.py

Problem this solves:
- CSV has an early line in the first few lyrics (e.g. line 3 at 22.36s)
- actual vocal in the audio is later (e.g. 26.62s)
- we want to bump JUST that early line (and nudge the next one forward so order is kept)

How it works:
1. read CSV (line,start,end)
2. run transcribe_window.py on 0..window_end (e.g. 0..40s)
3. for the first N CSV lyric rows (skip header) try to match CSV text to heard text
4. if heard time is >= csv time + min_bump → bump CSV time to heard time (+pad)
5. write CSV back
"""

import argparse
import csv
import os
import subprocess
import unicodedata
from typing import List, Dict, Tuple


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = " ".join(s.split())
    return s


def run_window_transcribe(
    scripts_dir: str,
    audio: str,
    start: float,
    end: float,
    language: str,
) -> List[Tuple[float, float, str]]:
    transcribe_py = os.path.join(scripts_dir, "transcribe_window.py")
    if not os.path.exists(transcribe_py):
        raise SystemExit(f"[ERR] {transcribe_py} not found")

    cmd = [
        "python3",
        transcribe_py,
        "--audio",
        audio,
        "--start",
        str(start),
        "--end",
        str(end),
        "--language",
        language,
    ]
    out = subprocess.check_output(cmd, encoding="utf-8", stderr=subprocess.DEVNULL)
    segs: List[Tuple[float, float, str]] = []
    for line in out.strip().splitlines():
        # expected: "26.50,28.90,Me dice que me ama"
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        try:
            s_f = float(parts[0])
            e_f = float(parts[1])
        except ValueError:
            continue
        text = parts[2].strip()
        if text:
            segs.append((s_f, e_f, text))
    segs.sort(key=lambda x: x[0])
    return segs


def read_csv_rows(path: str) -> List[Dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return list(r)


def write_csv_rows(path: str, rows: List[Dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def best_segment_for_text(
    text: str,
    segments: List[Tuple[float, float, str]],
) -> Tuple[float, float, str]:
    nt = normalize_text(text)
    for s, e, segtext in segments:
        ns = normalize_text(segtext)
        if not ns:
            continue
        if nt in ns or ns in nt:
            return s, e, segtext
    if segments:
        return segments[0]
    return 0.0, 0.0, ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True, help="mono 48k audio (same you render with)")
    ap.add_argument("--csv", required=True, help="CSV to fix (in-place)")
    ap.add_argument("--lyrics", required=True, help="lyrics txt (not used heavily, but required)")
    ap.add_argument("--scripts-dir", default="scripts", help="where transcribe_window.py lives")
    ap.add_argument("--window-end", type=float, default=40.0, help="listen 0..this seconds")
    ap.add_argument("--max-lines", type=int, default=6, help="how many early CSV lines to try to fix (after header)")
    ap.add_argument("--language", default="es")
    ap.add_argument("--min-bump", type=float, default=0.8, help="only move line if heard-start - csv-start >= this")
    ap.add_argument("--pad", type=float, default=0.05, help="extra sec after heard-start")
    args = ap.parse_args()

    rows = read_csv_rows(args.csv)
    if not rows:
        print("[fix] CSV empty, nothing to do")
        return

    segs = run_window_transcribe(
        args.scripts_dir,
        args.audio,
        0.0,
        args.window_end,
        args.language,
    )
    if not segs:
        print("[fix] no segments heard in 0–{:.1f}s, nothing to fix".format(args.window_end))
        return

    changed = False
    # start at row 1 because row 0 is your "Title//by//Artist"
    for idx in range(1, min(len(rows), args.max_lines + 1)):
        row = rows[idx]
        line_txt = row["line"].strip()
        try:
            csv_start = float(row["start"])
        except ValueError:
            continue

        heard_start, heard_end, heard_text = best_segment_for_text(line_txt, segs)
        delta = heard_start - csv_start

        if delta >= args.min_bump:
            new_start = heard_start + args.pad
            # keep duration roughly same
            old_end_str = row.get("end", "")
            try:
                old_end = float(old_end_str) if old_end_str else (csv_start + 2.0)
            except ValueError:
                old_end = csv_start + 2.0
            # push end forward by delta as well so it's not inverted
            new_end = max(old_end + delta, heard_end + args.pad)

            print(f"[fix] row {idx}: '{line_txt}' {csv_start:.2f} → {new_start:.2f} (heard '{heard_text}' at {heard_start:.2f})")
            row["start"] = f"{new_start:.2f}"
            row["end"] = f"{new_end:.2f}"
            changed = True

            # keep the next row from overlapping
            if idx + 1 < len(rows):
                nxt = rows[idx + 1]
                try:
                    nxt_start = float(nxt["start"])
                except ValueError:
                    nxt_start = new_end
                if nxt_start < new_end:
                    shift = new_end - nxt_start + 0.01
                    nxt["start"] = f"{nxt_start + shift:.2f}"
                    # adjust its end if needed
                    if nxt.get("end"):
                        try:
                            nxt_end = float(nxt["end"])
                        except ValueError:
                            nxt_end = nxt_start + 2.0
                        if nxt_end < float(nxt["start"]):
                            nxt["end"] = f"{nxt_end + shift:.2f}"
                    print(f"[fix]   also shifted row {idx+1} forward to keep order")

    if changed:
        write_csv_rows(args.csv, rows)
        print(f"[fix] wrote fixed CSV → {args.csv}")
    else:
        print("[fix] no early lines needed fixing")


if __name__ == "__main__":
    main()
# end of fix_early_lines_from_audio.py
