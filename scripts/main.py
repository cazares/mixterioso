#!/usr/bin/env python3
import sys
import argparse
import subprocess
from pathlib import Path

from common import log, PATHS
from step1_fetch import step1_fetch
from step2_split import step2_split
from step3_sync import step3_sync
from offset_tuner import tune_offset

# ─────────────────────────────────────────────
# Paths (flat scripts/ layout)
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
RENDERER = ROOT / "4_mp4.py"

if not RENDERER.exists():
    raise RuntimeError(f"Renderer not found at {RENDERER}")

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--confirm-offset", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    log(f"[MAIN] query={args.query}")

    # Step 1
    meta = step1_fetch(query=args.query, force=args.force)
    slug = meta["slug"]
    log(f"[MAIN] slug={slug}")

    # Step 2
    step2_split(slug=slug, force=args.force)

    # Step 3
    timing_info = step3_sync(slug=slug, force=args.force)

    offset = timing_info.get("default_offset", 0.0)

    if args.confirm_offset:
        offset = tune_offset(
            slug=slug,
            base_offset=offset,
            mixes_dir=PATHS["mixes"],
            timings_dir=PATHS["timings"],
            renderer_path=RENDERER,
        )

    # Step 4 — render via 4_mp4.py
    render_cmd = [
        sys.executable,
        str(RENDERER),
        "--slug", slug,
        "--offset", str(offset),
    ]
    subprocess.run(render_cmd, check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
# end of main.py
