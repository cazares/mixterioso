
#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# PATH SETUP (authoritative)
# ─────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

for p in (SCRIPTS_DIR, REPO_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from mix_utils import log
from scripts.fetch_timings_static import fetch_static_timings
from scripts.review_offset import review_offset
from scripts.manual_timing import run_manual_timing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    slug = args.slug

    if args.auto:
        log("TIMING", "Auto timing: static sources + offset review")
        ok = fetch_static_timings(slug)
        if ok:
            review_offset(slug)
        else:
            log("TIMING", "Static timing unavailable → manual timing")
            run_manual_timing(slug)
        return

    run_manual_timing(slug)


if __name__ == "__main__":
    main()
# end of 3_timing.py
