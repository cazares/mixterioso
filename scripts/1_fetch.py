
#!/usr/bin/env python3
"""
Step 1: Fetch using ONE YouTube query.
- Metadata-only search (flat playlist)
- Show top 12 results
- User selects ONE
- Extract artist/title/slug
- Delegate download to legacy 1_txt_mp3.py (NO --url arg)
"""
import argparse, subprocess, json, sys
from pathlib import Path
from mix_utils import log, slugify, fatal

def fmt_views(n):
    if not isinstance(n, int):
        return "?"
    for unit in ["","K","M","B","T"]:
        if abs(n) < 1000:
            return f"{n}{unit}"
        n //= 1000
    return f"{n}P"

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    args = parser.parse_args()

    cmd = [
        "yt-dlp",
        "ytsearch12:" + args.query,
        "--flat-playlist",
        "--skip-download",
        "--dump-single-json",
    ]
    data = json.loads(subprocess.check_output(cmd, text=True))
    entries = data.get("entries") or []
    if not entries:
        fatal("No YouTube results found.")

    print("\nSelect YouTube source:\n")
    for i, e in enumerate(entries, start=1):
        title = e.get("title","?")
        uploader = e.get("uploader","?")
        views = fmt_views(e.get("view_count"))
        print(f" {i:2d}) {title} — {uploader} ({views})")

    print()
    while True:
        try:
            choice = input("Choose 1–12: ").strip()
        except EOFError:
            fatal("No selection made.")
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(entries):
                break
        print("Invalid choice.")

    sel = entries[idx-1]
    title = sel.get("title") or args.query
    artist = sel.get("uploader") or "Unknown Artist"
    slug = slugify(f"{artist} {title}")

    log("META", f"{artist} - {title}")
    log("META", f"Slug: {slug}")

    legacy = Path(__file__).with_name("1_txt_mp3.py")
    if not legacy.exists():
        fatal("Original 1_txt_mp3.py not found.")

    # IMPORTANT: legacy script does NOT accept --url
    subprocess.run(
        [
            sys.executable,
            str(legacy),
            "--artist", artist,
            "--title", title,
            "--slug", slug,
        ],
        check=True,
    )

if __name__ == "__main__":
    run()
# end of 1_fetch.py
