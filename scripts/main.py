#!/usr/bin/env python3
"""Mixterioso — simplified pipeline entrypoint.

Runs steps 1–4 sequentially.

Usage examples:
  python3 scripts/main.py --query "Mazzy Star - Fade Into You"
  python3 scripts/main.py --query "Mazzy Star - Fade Into You" -f
  python3 scripts/main.py --query "Mazzy Star - Fade Into You" -c
  python3 scripts/main.py --query "Mazzy Star - Fade Into You" --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Ensure repo root is on import path
SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from mixterioso.common import IOFlags, Paths, log, parse_query, slugify
from mixterioso.step1_fetch import step1_fetch
from mixterioso.step2_split import step2_split
from mixterioso.step3_sync import step3_sync
from mixterioso.step4_build import step4_build


def parse_args(argv=None):
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--query", required=True, help='Format: "Artist - Title"')
    p.add_argument("-f", "--force", action="store_true", help="Overwrite without prompts")
    p.add_argument("-c", "--confirm", action="store_true", help="Prompt before overwrite")
    p.add_argument("--dry-run", action="store_true", help="Show actions without writing")
    p.add_argument("--mix", choices=["full", "instrumental", "stems"], default="full", help="Audio mix mode (default: full)")
    p.add_argument("--vocals-db", type=float, default=0.0, help="Stem gain (dB) for vocals when mixing from stems")
    p.add_argument("--bass-db", type=float, default=0.0, help="Stem gain (dB) for bass when mixing from stems")
    p.add_argument("--drums-db", type=float, default=0.0, help="Stem gain (dB) for drums when mixing from stems")
    p.add_argument("--other-db", type=float, default=0.0, help="Stem gain (dB) for other when mixing from stems")
    p.add_argument("--offset", type=float, default=None, help="Override subtitle timing offset in seconds")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    flags = IOFlags(force=args.force, confirm=args.confirm, dry_run=args.dry_run)

    artist, title = parse_query(args.query)
    slug = slugify(title)

    paths = Paths.from_scripts_dir(Path(__file__).resolve())

    log("MAIN", f"query={args.query}")
    log("MAIN", f"artist={artist}")
    log("MAIN", f"title={title}")
    log("MAIN", f"slug={slug}")

    s1 = step1_fetch(paths, query=args.query, artist=artist, title=title, slug=slug, flags=flags)

    # Step2 produces mixes/<slug>.*; it can fall back to mp3 copy.
    step2_split(paths, slug=slug, mix_mode=args.mix, vocals_db=args.vocals_db, bass_db=args.bass_db, drums_db=args.drums_db, other_db=args.other_db, flags=flags)

    # Step3 prefers LRC then VTT.
    s3_source = step3_sync(paths, slug=slug, flags=flags)

    # Offset default: if user didn't override, use 1.0 for LRC (per preference), else 0.0.
    if args.offset is not None:
        offset = float(args.offset)
    else:
        offset = 1.0 if s3_source == "lrc" else 0.0

    step4_build(paths, slug=slug, offset=offset, flags=flags)

    log("MAIN", "Done", color="\033[32m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# end of main.py
