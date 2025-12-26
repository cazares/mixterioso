#!/usr/bin/env python3
import sys
from pathlib import Path

# ─────────────────────────────────────────────
# Bootstrap import path (repo root)
# ─────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""
Fetch captions (VTT) via yt-dlp into timings/<slug>.<lang>.vtt

Last resort: if no captions, do nothing.

Requires: yt-dlp
"""

import argparse
import subprocess
from pathlib import Path

try:
    from mix_utils import PATHS, log, CYAN, GREEN, YELLOW, RED
except Exception:
    def log(tag, msg, color=""):
        print(f"[{tag}] {msg}")
    CYAN = GREEN = YELLOW = RED = ""
    ROOT = Path(__file__).resolve().parent.parent
    PATHS = {"timings": ROOT / "timings"}

TIMINGS_DIR = Path(PATHS["timings"])

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", default="")
    ap.add_argument("--title", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--lang", default="en")
    return ap.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    TIMINGS_DIR.mkdir(parents=True, exist_ok=True)

    query = f"{(args.artist or '').strip()} {(args.title or '').strip()}".strip()
    target = f"ytsearch1:{query}"
    out_tmpl = str(TIMINGS_DIR / f"{args.slug}.%(ext)s")

    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-format", "vtt",
        "--sub-langs", args.lang,
        "-o", out_tmpl,
        "--no-playlist",
        target,
    ]
    log("captions", " ".join(cmd), CYAN)
    try:
        subprocess.run(cmd, check=True)
    except Exception as e:
        log("captions", f"yt-dlp failed: {e}", YELLOW)
        return 0

    vtts = sorted(TIMINGS_DIR.glob(f"{args.slug}*.vtt"))
    if vtts:
        log("captions", f"Wrote {vtts[-1]}", GREEN)
    else:
        log("captions", "No captions", YELLOW)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
# end of 1_fetch_captions_vtt.py
