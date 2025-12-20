
#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
SCRIPTS_DIR = THIS_FILE.parent

PY = sys.executable

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="Single YouTube search query")
    args = ap.parse_args()

    # Very simple heuristic: last word(s) after artist assumed title
    # For now, pass full query as title; artist unknown is acceptable for adapter
    query = args.query.strip()

    # Try naive split: assume 'Artist Title'
    parts = query.split()
    if len(parts) >= 2:
        artist = " ".join(parts[:-1])
        title = parts[-1]
    else:
        artist = "Unknown Artist"
        title = query

    slug = f"{artist}_{title}".lower().replace(" ", "_")

    cmd = [
        PY,
        str(SCRIPTS_DIR / "1_txt_mp3.py"),
        "--artist", artist,
        "--title", title,
        "--slug", slug,
    ]

    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    main()
# end of 1_fetch.py
