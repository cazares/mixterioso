
#!/usr/bin/env python3
"""
Step 1: Fetch using ONE YouTube query.
- Metadata-only search (NO downloads, NO format probing)
- Shows top 12 YouTube results
- User selects ONE
- Only then proceeds to real download via legacy script
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

    # 🔑 metadata-only search (flat, no probing)
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

    video_id = sel.get("id")
    if not video_id:
        fatal("Selected entry missing video id.")

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    title = sel.get("title") or args.query
    artist = sel.get("uploader") or "Unknown Artist"
    slug = slugify(f"{artist} {title}")

    log("META", f"{artist} - {title}")
    log("META", f"Slug: {slug}")

    legacy = Path(__file__).with_name("1_txt_mp3.py")
    if not legacy.exists():
        fatal("Original 1_txt_mp3.py not found.")

    subprocess.run(
        [
            sys.executable,
            str(legacy),
            "--artist", artist,
            "--title", title,
            "--slug", slug,
            "--url", video_url,
        ],
        check=True,
    )

if __name__ == "__main__":
    run()
# end of 1_fetch.py
