#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_auto_sync_lyrics.py — auto or interactive lyric timing
Updated 2025-10-21:
✅ Adds UTF-8 safe lyric reading
✅ Adds clean_text() normalization
✅ User starts the clock manually
✅ Writes UTF-8 CSV
"""

import argparse, csv, time, sys
from pathlib import Path
import re

# ───────────────────────────────────────────────────────────────
def sanitize_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s.strip().replace(" ", "_"))

def clean_text(s: str) -> str:
    """Normalize lyric text to pure UTF-8 and remove invisible characters."""
    return (
        s.encode("utf-8", "ignore")
        .decode("utf-8")
        .replace("\uFEFF", "")
        .replace("\uFFFD", "")
        .replace("\r", "")
        .replace("\xa0", " ")
        .strip()
    )

# ───────────────────────────────────────────────────────────────
def run_interactive(artist: str, title: str, lyrics_path: Path, out_csv: Path):
    print(f"\n🎤 Interactive mode for '{title}' by {artist}")
    print(f"📝 Using lyrics file: {lyrics_path}")
    print("💡 Press [Enter] for each lyric line as it’s sung. Ctrl-C to abort.\n")

    if not lyrics_path.exists():
        print(f"❌ Lyrics file not found: {lyrics_path}")
        sys.exit(1)

    with open(lyrics_path, encoding="utf-8") as f:
        lines = [clean_text(l) for l in f if l.strip()]

    if not lines:
        print("❌ No lyrics found in file.")
        sys.exit(1)

    input("⏯️  Ready when you are. Press [Enter] to start timing… ")
    start = time.time()
    rows = []
    for i, line in enumerate(lines, 1):
        input(f"{i:02d}. {line}\n   ▶️  Press [Enter] when sung...")
        t = time.time() - start
        rows.append((f"{t:.2f}", line))

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "text"])
        w.writerows(rows)

    print(f"\n✅ Saved to {out_csv}")

# ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artist", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--vocals-percent", type=float, default=0.0)
    parser.add_argument("--interactive", action="store_true", help="Enable tap-timing mode")
    args = parser.parse_args()

    artist_slug = sanitize_name(args.artist)
    title_slug = sanitize_name(args.title)
    lyrics_path = Path("lyrics") / f"{artist_slug}_{title_slug}.txt"
    out_csv = Path("lyrics") / f"{artist_slug}_{title_slug}_synced.csv"

    if args.interactive:
        run_interactive(args.artist, args.title, lyrics_path, out_csv)
    else:
        print("⚙️  Non-interactive mode not implemented here.")
        sys.exit(0)

if __name__ == "__main__":
    main()
