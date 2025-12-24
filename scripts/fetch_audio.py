#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------
# Utils
# ---------------------------------------------------------
def log(tag, msg):
    print(f"[{tag}] {msg}")

def run(cmd, capture=False):
    if capture:
        return subprocess.check_output(cmd, text=True)
    subprocess.run(cmd, check=True)

def format_duration(seconds):
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def format_views(v):
    if not v:
        return "?"
    if v >= 1_000_000_000:
        return f"{v/1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}K"
    return str(v)

# ---------------------------------------------------------
# YouTube search via yt-dlp
# ---------------------------------------------------------
def search_youtube(query, limit=5):
    cmd = [
        "yt-dlp",
        f"ytsearch{limit}:{query}",
        "--dump-json",
        "--no-playlist",
    ]

    raw = run(cmd, capture=True)
    results = []

    for line in raw.splitlines():
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        results.append({
            "id": data.get("id"),
            "title": data.get("title"),
            "duration": data.get("duration"),
            "view_count": data.get("view_count"),
            "uploader": data.get("uploader"),
            "webpage_url": data.get("webpage_url"),
        })

    return results

# ---------------------------------------------------------
# Audio download
# ---------------------------------------------------------
def download_audio(url, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "-o", str(out_path),
        url,
    ]

    run(cmd)

# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True, help="YouTube search query")
    p.add_argument("--out", required=True, help="Output MP3 path")
    p.add_argument("--yes", action="store_true", help="Auto-select first result")
    args = p.parse_args()

    out = Path(args.out)

    log("SEARCH", f"Searching YouTube for: {args.query}")
    results = search_youtube(args.query, limit=5)

    if not results:
        raise SystemExit("No YouTube results found")

    print("\nTop YouTube results:\n")
    for i, r in enumerate(results, 1):
        print(
            f"{i}) {r['title']}\n"
            f"   Channel : {r['uploader']}\n"
            f"   Duration: {format_duration(r['duration'])}\n"
            f"   Views   : {format_views(r['view_count'])}\n"
        )

    if args.yes or not sys.stdin.isatty():
        choice = 1
        log("AUTO", "Auto-selecting result #1")
    else:
        while True:
            try:
                choice = input(f"Choose 1–{len(results)} (q=quit): ").strip()
                if choice.lower() == "q":
                    sys.exit(0)
                choice = int(choice)
                if 1 <= choice <= len(results):
                    break
            except ValueError:
                pass
            print("Invalid selection")

    selected = results[choice - 1]
    log("DOWNLOAD", f"Downloading: {selected['title']}")

    download_audio(selected["webpage_url"], out)

    if not out.exists():
        raise SystemExit("Download failed")

    log("OK", f"Audio written to {out}")

if __name__ == "__main__":
    main()

# end of fetch_audio.py
