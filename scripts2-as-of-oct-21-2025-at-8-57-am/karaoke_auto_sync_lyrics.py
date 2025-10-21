#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_auto_sync_lyrics.py — auto or interactive lyric timing

Stable extension:
 - Adds --interactive mode for tap-timing.
 - Reuses lyrics/<artist>_<title>.txt
 - Outputs lyrics/<artist>_<title>_synced.csv
"""

import argparse, csv, os, sys, time
from pathlib import Path

# -------------------------------------------------------------
def sanitize_name(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_]+", "_", s.strip().replace(" ", "_"))

# -------------------------------------------------------------
def run_interactive(artist: str, title: str, lyrics_path: Path, out_csv: Path):
    print(f"\n🎤 Interactive mode for '{title}' by {artist}")
    print(f"📝 Using lyrics file: {lyrics_path}")
    print("💡 Press [Enter] for each lyric line as it’s sung. Ctrl-C to abort.\n")

    with open(lyrics_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    start = time.time()
    rows = []

    for i, line in enumerate(lines, 1):
        input(f"{i:02d}. {line}\n   ▶️  Press [Enter] when sung...")
        t = time.time() - start
        rows.append((f"{t:.2f}", line))

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "text"])
        writer.writerows(rows)

    print(f"\n✅ Saved interactive timing to {out_csv}")

# -------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Auto or interactive lyric timing")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--vocals-percent", type=float, default=0.0)
    p.add_argument("--interactive", action="store_true", help="Enable tap-timing mode")
    args = p.parse_args()

    artist_slug = sanitize_name(args.artist)
    title_slug = sanitize_name(args.title)
    lyrics_path = Path("lyrics") / f"{artist_slug}_{title_slug}.txt"
    out_csv = Path("lyrics") / f"{artist_slug}_{title_slug}_synced.csv"

    if args.interactive:
        run_interactive(args.artist, args.title, lyrics_path, out_csv)
    else:
        print("⚙️  (Placeholder) Non-interactive Whisper flow not implemented here.")
        sys.exit(0)

if __name__ == "__main__":
    main()

# end of karaoke_auto_sync_lyrics.py
