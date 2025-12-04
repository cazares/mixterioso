#!/usr/bin/env python3
# scripts/convert_timings_to_index_secs_text.py
from __future__ import annotations
import argparse
from pathlib import Path
from timings_io import load_timings_any, save_timings_canonical

def fix_one(p: Path) -> bool:
    try:
        triples = load_timings_any(p)
        save_timings_canonical(p, triples)
        print(f"Fixed: {p} ({len(triples)} rows)")
        return True
    except Exception as e:
        print(f"[skip] {p}: {e}")
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="timings", help="Directory to scan (default: timings)")
    ap.add_argument("--file", help="Convert a single CSV")
    args = ap.parse_args()

    if args.file:
        ok = fix_one(Path(args.file))
        raise SystemExit(0 if ok else 1)

    d = Path(args.dir)
    if not d.exists():
        raise SystemExit(f"Missing directory: {d}")

    any_ok = False
    for p in sorted(d.glob("*.csv")):
        ok = fix_one(p)
        any_ok = any_ok or ok
    raise SystemExit(0 if any_ok else 1)

if __name__ == "__main__":
    main()
# end of scripts/convert_timings_to_index_secs_text.py
