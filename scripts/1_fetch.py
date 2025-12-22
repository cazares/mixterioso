#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

from yt_dlp import YoutubeDL

# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────
THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from mix_utils import log

# ─────────────────────────────────────────────
# YouTube search + picker
# ─────────────────────────────────────────────
def search_youtube(query, limit=12):
    ydl_opts = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        return result.get("entries", [])


def pick(items):
    print("\nSelect YouTube source:\n")
    for i, item in enumerate(items, 1):
        title = item.get("title", "Unknown title")
        dur = item.get("duration")

        if isinstance(dur, (int, float)):
            total = int(dur)
            mm = total // 60
            ss = total % 60
            dur_str = f"{mm}:{ss:02d}"
        else:
            dur_str = "?:??"

        print(f" {i:2d}) {title} ({dur_str})")

    print()
    choice = input(f"Choose 1–{len(items)}: ").strip()
    idx = int(choice) - 1
    return items[idx]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    args = ap.parse_args()

    items = search_youtube(args.query)
    if not items:
        raise RuntimeError("No YouTube results found")

    picked = pick(items)
    url = picked.get("url") or picked.get("webpage_url")

    if not url:
        raise RuntimeError("Selected item has no URL")

    # Hand off to Step 1 downloader
    subprocess.run(
        [
            sys.executable,
            SCRIPTS_DIR / "1_txt_mp3.py",
            "--artist",
            "UNKNOWN",
            "--title",
            picked.get("title", "UNKNOWN"),
            "--slug",
            picked.get("id", "unknown"),
            "--url",
            url,
        ],
        check=True,
    )


if __name__ == "__main__":
    main()

# end of 1_fetch.py
