#!/usr/bin/env python3
import argparse
import os
import re
import sys
from pathlib import Path
import requests
from dotenv import load_dotenv

# ---------- utils ----------
def log(tag, msg):
    print(f"[{tag}] {msg}")

def clean_lyrics(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ---------- Musixmatch ----------
def musixmatch_fetch(artist, title):
    api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not api_key:
        return ""

    try:
        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.search",
            params={
                "apikey": api_key,
                "q_artist": artist,
                "q_track": title,
                "page_size": 1,
                "f_has_lyrics": 1,
            },
            timeout=10,
        )
        data = r.json()
        body = data.get("message", {}).get("body", {})
        tracks = body.get("track_list", [])
        if not tracks:
            return ""

        track_id = tracks[0]["track"]["track_id"]

        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.lyrics.get",
            params={"apikey": api_key, "track_id": track_id},
            timeout=10,
        )
        lyrics = (
            r.json()
            .get("message", {})
            .get("body", {})
            .get("lyrics", {})
            .get("lyrics_body", "")
        )

        footer = "******* This Lyrics is NOT for Commercial use *******"
        if footer in lyrics:
            lyrics = lyrics.split(footer)[0]

        return clean_lyrics(lyrics)
    except Exception:
        return ""

# ---------- LRCLIB ----------
def lrclib_fetch(artist, title):
    try:
        r = requests.get(
            "https://lrclib.net/api/get",
            params={"artist_name": artist, "track_name": title},
            timeout=10,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        return clean_lyrics(data.get("plainLyrics", ""))
    except Exception:
        return ""

# ---------- Genius ----------
def genius_fetch(artist, title):
    try:
        q = f"{artist} {title} lyrics"
        r = requests.get(
            "https://genius.com/api/search/multi",
            params={"q": q},
            timeout=10,
        )
        hits = r.json()["response"]["sections"][0]["hits"]
        if not hits:
            return ""

        url = hits[0]["result"]["url"]
        html = requests.get(url, timeout=10).text
        blocks = re.findall(r'<div data-lyrics-container="true">(.*?)</div>', html, re.S)
        text = "\n".join(re.sub(r"<.*?>", "", b) for b in blocks)
        return clean_lyrics(text)
    except Exception:
        return ""

# ---------- main ----------
def main():
    load_dotenv()

    p = argparse.ArgumentParser()
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    for name, fn in [
        ("MUSIXMATCH", musixmatch_fetch),
        ("LRCLIB", lrclib_fetch),
        ("GENIUS", genius_fetch),
    ]:
        log("TRY", name)
        lyrics = fn(args.artist, args.title)
        if lyrics:
            out.write_text(lyrics, encoding="utf-8")
            log("OK", f"Lyrics written via {name}")
            return

    out.write_text("", encoding="utf-8")
    log("WARN", "No lyrics found; wrote empty file")

if __name__ == "__main__":
    main()

# end of fetch_lyrics.py
