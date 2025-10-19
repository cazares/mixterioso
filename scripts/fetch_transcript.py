#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_transcript.py — fetch YouTube transcript as (timestamp,text) CSV
Stable hybrid version:
 ✅ Auto-fetches YouTube video ID if not provided
 ✅ Works with all youtube-transcript-api versions
 ✅ Gracefully handles disabled captions
"""

import argparse, csv, sys, importlib, importlib.util, json, requests
from pathlib import Path

# --- 1️⃣ Safe import of youtube_transcript_api -------------------------------
def verify_import_path():
    spec = importlib.util.find_spec("youtube_transcript_api")
    if not spec:
        print("❌ youtube-transcript-api not installed. Run: pip3 install youtube-transcript-api==0.6.1")
        sys.exit(1)
    mod_path = Path(spec.origin)
    print(f"✅ youtube_transcript_api loaded from: {mod_path}")
    return spec

verify_import_path()

yt_module = importlib.import_module("youtube_transcript_api")
YTA = getattr(yt_module, "YouTubeTranscriptApi", None)
TranscriptsDisabled = getattr(yt_module, "TranscriptsDisabled", type("TD", (), {}))
NoTranscriptFound = getattr(yt_module, "NoTranscriptFound", type("NF", (), {}))

api_style = (
    "list" if hasattr(YTA, "list_transcripts")
    else "classic" if hasattr(YTA, "get_transcript")
    else "api_submodule" if hasattr(getattr(yt_module, "api", None), "YouTubeTranscriptApi")
    else None
)

if not api_style:
    print("❌ Could not detect usable YouTubeTranscriptApi interface.")
    sys.exit(1)
print(f"🔧 Detected API mode: {api_style}")

# --- 2️⃣ YouTube ID resolution -----------------------------------------------
def get_youtube_id(artist: str, title: str, api_key: str) -> str:
    """Query YouTube Data API for top result matching artist+title."""
    query = f"{artist} {title}"
    url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=1&q={query}&key={api_key}"
    print(f"🔎 Resolving YouTube ID for '{query}'…")
    r = requests.get(url)
    if r.status_code != 200:
        print(f"❌ YouTube API error {r.status_code}: {r.text[:200]}")
        sys.exit(1)
    items = r.json().get("items", [])
    if not items:
        print("❌ No YouTube results found.")
        sys.exit(1)
    vid = items[0]["id"]["videoId"]
    print(f"🎥 Found YouTube video: https://www.youtube.com/watch?v={vid}")
    return vid

# --- 3️⃣ Transcript fetcher ---------------------------------------------------
def fetch_transcript(yid: str):
    """Auto-adapts to any youtube-transcript-api variant."""
    if api_style == "list":
        inst = YTA.list_transcripts(yid)
        for lang in ["en", "en-US", "auto"]:
            try:
                t = inst.find_transcript([lang])
                return t.fetch()
            except Exception:
                continue
        return next(iter(inst)).fetch()

    elif api_style == "classic":
        for lang in ["en", "en-US", "auto"]:
            try:
                return YTA.get_transcript(yid, languages=[lang])
            except Exception:
                continue
        return YTA.get_transcript(yid)

    elif api_style == "api_submodule":
        subapi = yt_module.api.YouTubeTranscriptApi
        for lang in ["en", "en-US", "auto"]:
            try:
                return subapi.get_transcript(yid, languages=[lang])
            except Exception:
                continue
        return subapi.get_transcript(yid)

# --- 4️⃣ Main ---------------------------------------------------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--youtube-id", help="(optional) explicit YouTube ID")
    p.add_argument("--youtube-api-key", help="API key (or env $YT_KEY)")
    args = p.parse_args()

    api_key = args.youtube_api_key or os.getenv("YT_KEY")
    if not api_key:
        print("❌ Missing YouTube API key. Export YT_KEY or pass --youtube-api-key.")
        sys.exit(1)

    yid = args.youtube_id or get_youtube_id(args.artist, args.title, api_key)
    print(f"🌐 Fetching transcript for {yid}…")

    try:
        data = fetch_transcript(yid)
        if not data:
            print("❌ No transcript data found.")
            sys.exit(1)

        out_path = Path(args.out)
        out_path.parent.mkdir(exist_ok=True)
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "text"])
            for entry in data:
                w.writerow([f"{entry['start']:.2f}", entry["text"].replace('\n', '\\N')])

        print(f"✅ Transcript saved to: {out_path}")

    except (TranscriptsDisabled, NoTranscriptFound):
        print("❌ Transcript unavailable — captions disabled for this video.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    import os
    main()

# end of fetch_transcript.py
