#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
karaoke_auto_sync_lyrics.py — explicit or relative lyric timing helper
Simplified 2025-10-21:
✅ Honors provided filename (like 'soltera_short2.txt')
✅ Searches current directory first, then ./lyrics/
✅ No artist/title-based naming at all
"""

import argparse, csv, time, sys
from pathlib import Path

def run_interactive(lyrics_path: Path):
    print(f"\n🎤 Interactive lyric timing")
    print(f"📝 Using lyrics file: {lyrics_path}")
    print("💡 Press [Enter] for each lyric line as it’s sung. Ctrl-C to abort.\n")

    if not lyrics_path.exists():
        print(f"❌ Lyrics file not found: {lyrics_path}")
        sys.exit(1)

    with open(lyrics_path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    print("⏯️  Ready. Press [Enter] to start.")
    input()
    start = time.time()
    rows = []
    for i, line in enumerate(lines, 1):
        input(f"{i:02d}. {line}\n   ▶️  Press [Enter] when sung...")
        t = time.time() - start
        rows.append((f"{t:.2f}", line))

    out_csv = lyrics_path.with_name(f"{lyrics_path.stem}_synced.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "text"])
        w.writerows(rows)

    print(f"\n✅ Saved to {out_csv}")

def main():
    parser = argparse.ArgumentParser(description="Interactive lyric timer (explicit or relative filename)")
    parser.add_argument("--lyrics", required=True, help="Lyrics text filename or path")
    parser.add_argument("--interactive", action="store_true", help="Enable tap timing mode")
    args = parser.parse_args()

    # Resolve filename: check current dir, then ./lyrics/
    lyrics_path = Path(args.lyrics)
    if not lyrics_path.exists():
        alt = Path("lyrics") / args.lyrics
        if alt.exists():
            lyrics_path = alt
        else:
            print(f"❌ Could not find {args.lyrics} in current directory or ./lyrics/")
            sys.exit(1)

    run_interactive(lyrics_path)

if __name__ == "__main__":
    main()

# end of karaoke_auto_sync_lyrics.py
