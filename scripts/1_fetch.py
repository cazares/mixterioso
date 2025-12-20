
#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent
REPO_ROOT = SCRIPTS_DIR.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mix_utils import log, slugify
from yt_search import select_youtube_video
from lyrics import fetch_lyrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    args = ap.parse_args()

    query = args.query

    # 1) YouTube selection + metadata
    meta = select_youtube_video(query)
    artist = meta.get("artist") or "Unknown Artist"
    title = meta.get("title") or query
    slug = slugify(f"{artist}_{title}")

    log("META", f"{artist} - {title}")
    log("META", f"Slug: {slug}")

    # 2) Lyrics + audio handled internally (existing helpers)
    fetch_lyrics(artist, title, slug, meta)

if __name__ == "__main__":
    main()
# end of 1_fetch.py
