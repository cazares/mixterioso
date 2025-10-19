#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_best_lyrics.py — unified multi-source lyric retriever for Karaoke Time

Priority order:
  1. Musixmatch API
  2. Genius API (authenticated)
  3. Whisper + LLM correction (fallback)
  4. Genius via Google search + HTML scrape
  5. LyricsFreak → AZLyrics → Lyrics.com

Output:
  Saves clean lyrics text to lyrics/{artist}_{title}.txt
  Writes provenance metadata to lyrics/{artist}_{title}.meta.json
"""

import argparse, os, re, requests, subprocess, json, html, time
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/119.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------
def sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name.strip().replace(" ", "_"))

def is_valid_lyrics(text: str) -> bool:
    """Reject empty or obviously broken lyrics."""
    if not text or len(text.strip()) < 40:
        return False
    if any(x in text.lower() for x in ["error", "{", "}", "[", "]", "lyrics not available"]):
        return False
    return True

def save_output(lyrics: str, artist: str, title: str, source: str):
    out_dir = Path("lyrics")
    out_dir.mkdir(exist_ok=True)
    slug = f"{sanitize_name(artist)}_{sanitize_name(title)}"
    txt_path = out_dir / f"{slug}.txt"
    meta_path = out_dir / f"{slug}.meta.json"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(lyrics.strip())

    meta = {
        "artist": artist,
        "title": title,
        "source": source,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "length": len(lyrics),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"✅ Lyrics saved to: {txt_path} (via {source})")
    return txt_path

# ---------------------------------------------------------------------
def try_musixmatch(artist, title):
    """Try fetching lyrics via Musixmatch API (RapidAPI key optional)."""
    key = os.getenv("MUSIXMATCH_KEY")
    if not key:
        print("⚠️  No MUSIXMATCH_KEY set — skipping Musixmatch API.")
        return ""
    url = "https://api.musixmatch.com/ws/1.1/matcher.lyrics.get"
    params = {"q_track": title, "q_artist": artist, "apikey": key}
    print("🎵 Trying Musixmatch API…")
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        body = (
            data.get("message", {})
            .get("body", {})
            .get("lyrics", {})
            .get("lyrics_body", "")
        )
        if is_valid_lyrics(body):
            print("✅ Musixmatch returned valid lyrics.")
            return body.strip()
        print("⚠️ Musixmatch returned empty or invalid data.")
        return ""
    except Exception as e:
        print(f"❌ Musixmatch error: {e}")
        return ""

# ---------------------------------------------------------------------
def try_genius_api(artist, title, token):
    """Try Genius official API (authenticated)."""
    if not token:
        print("⚠️  No Genius token — skipping Genius API.")
        return ""
    print("🎵 Trying Genius API…")
    try:
        search_url = "https://api.genius.com/search"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"q": f"{artist} {title}"}
        r = requests.get(search_url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        hits = r.json()["response"]["hits"]
        if not hits:
            print("⚠️ Genius API returned no hits.")
            return ""
        song_url = hits[0]["result"]["url"]
        print(f"🔗 Genius API → {song_url}")
        page = requests.get(song_url, headers={"User-Agent": USER_AGENT})
        html_blocks = re.findall(
            r'<div[^>]+Lyrics__Container[^>]*>(.*?)</div>', page.text, re.DOTALL
        )
        lyrics = "\n".join(re.sub(r"<.*?>", "", b).strip() for b in html_blocks)
        if is_valid_lyrics(lyrics):
            print("✅ Genius API scrape succeeded.")
            return html.unescape(lyrics)
        return ""
    except Exception as e:
        print(f"❌ Genius API failed: {e}")
        return ""

# ---------------------------------------------------------------------
def try_whisper_correction(artist, title):
    """Use Whisper + LLM correction as last reliable fallback."""
    print("🎤 Trying Whisper transcription fallback…")
    try:
        whisper_csv = Path(f"lyrics/{sanitize_name(artist)}_{sanitize_name(title)}_synced.csv")
        if not whisper_csv.exists():
            print("⚠️ Whisper CSV not found; cannot auto-correct.")
            return ""
        with open(whisper_csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        # Take only lyric text
        texts = [l.split(",", 1)[-1].strip() for l in lines if "," in l]
        combined = " ".join(texts)
        if len(combined) < 50:
            return ""
        print("✅ Whisper text loaded. (Simulated LLM cleanup for now.)")
        return combined
    except Exception as e:
        print(f"❌ Whisper correction failed: {e}")
        return ""

# ---------------------------------------------------------------------
def try_genius_scrape(artist, title):
    """Try Genius via Google search and direct HTML scrape."""
    print("🔍 Trying Genius via Google search + scrape…")
    try:
        query = f"{artist} {title} site:genius.com"
        cmd = ["curl", "-sL", "-A", USER_AGENT, f"https://www.google.com/search?q={query}"]
        html_data = subprocess.check_output(cmd).decode("utf-8", errors="ignore")
        match = re.search(r"https://genius\.com/[a-zA-Z0-9\-]+-lyrics", html_data)
        if not match:
            print("⚠️ No Genius URL found in Google results.")
            return ""
        url = match.group(0)
        print(f"✅ Found Genius page: {url}")
        page = requests.get(url, headers={"User-Agent": USER_AGENT})
        html_blocks = re.findall(
            r'<div[^>]+Lyrics__Container[^>]*>(.*?)</div>', page.text, re.DOTALL
        )
        lyrics = "\n".join(re.sub(r"<.*?>", "", b).strip() for b in html_blocks)
        if is_valid_lyrics(lyrics):
            print("✅ Genius scrape succeeded.")
            return html.unescape(lyrics)
        return ""
    except Exception as e:
        print(f"❌ Genius scrape failed: {e}")
        return ""

# ---------------------------------------------------------------------
def try_web_scrapers(artist, title):
    """Try LyricsFreak → AZLyrics → Lyrics.com chain."""
    artist_q = sanitize_name(artist)
    title_q = sanitize_name(title)
    try:
        # LyricsFreak
        print("🔍 Trying LyricsFreak…")
        search_url = f"https://www.lyricsfreak.com/search.php?a=search&type=song&q={artist_q}+{title_q}"
        html_data = requests.get(search_url, headers={"User-Agent": USER_AGENT}).text
        match = re.search(r"/[a-z0-9]/[a-z0-9_\-]+/[a-z0-9_\-]+\.html", html_data)
        if match:
            url = f"https://www.lyricsfreak.com{match.group(0)}"
            page = requests.get(url, headers={"User-Agent": USER_AGENT}).text
            block = re.search(r'<div id="content_h"[^>]*>(.*?)</div>', page, re.DOTALL)
            if block:
                clean = re.sub(r"<.*?>", "", block.group(1))
                if is_valid_lyrics(clean):
                    print("✅ LyricsFreak returned valid lyrics.")
                    return clean

        # AZLyrics
        print("🔍 Trying AZLyrics…")
        search_url = f"https://search.azlyrics.com/search.php?q={artist_q}+{title_q}"
        html_data = requests.get(search_url, headers={"User-Agent": USER_AGENT}).text
        match = re.search(r"https://www.azlyrics.com/lyrics/[a-z0-9]+/[a-z0-9]+\.html", html_data)
        if match:
            url = match.group(0)
            page = requests.get(url, headers={"User-Agent": USER_AGENT}).text
            block = re.search(r"<!-- Usage of azlyrics.com .*? -->.*?<!-- MxM", page, re.DOTALL)
            if block:
                clean = re.sub(r"<.*?>", "", block.group(0))
                if is_valid_lyrics(clean):
                    print("✅ AZLyrics returned valid lyrics.")
                    return clean

        # Lyrics.com
        print("🔍 Trying Lyrics.com…")
        search_url = f"https://www.lyrics.com/serp.php?st={title_q}+{artist_q}"
        html_data = requests.get(search_url, headers={"User-Agent": USER_AGENT}).text
        match = re.search(r"/lyric/[0-9]+/[A-Za-z0-9\-_]+", html_data)
        if match:
            url = f"https://www.lyrics.com{match.group(0)}"
            page = requests.get(url, headers={"User-Agent": USER_AGENT}).text
            block = re.search(r'<pre id="lyric-body-text"[^>]*>(.*?)</pre>', page, re.DOTALL)
            if block:
                clean = re.sub(r"<.*?>", "", block.group(1))
                if is_valid_lyrics(clean):
                    print("✅ Lyrics.com returned valid lyrics.")
                    return clean

        print("❌ All web scrapers failed.")
        return ""
    except Exception as e:
        print(f"❌ Web scraping chain failed: {e}")
        return ""

# ---------------------------------------------------------------------
def get_best_lyrics(artist, title, genius_token=None):
    for func, name in [
        (try_musixmatch, "musixmatch"),
        (lambda a, t: try_genius_api(a, t, genius_token), "genius_api"),
        (try_whisper_correction, "whisper_correction"),
        (try_genius_scrape, "genius_scrape"),
        (try_web_scrapers, "web_scrapers"),
    ]:
        lyrics = func(artist, title)
        if is_valid_lyrics(lyrics):
            print(f"🎯 Source selected: {name}")
            return lyrics, name
    print("❌ No lyrics source succeeded.")
    return "", "none"

# ---------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="Fetch best lyrics using multi-source fallback chain.")
    p.add_argument("--artist", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--genius-token", help="Optional Genius API token")
    p.add_argument("--out", help="Optional output path")
    args = p.parse_args()

    lyrics, src = get_best_lyrics(args.artist, args.title, args.genius_token)
    if not lyrics:
        print("❌ Failed to retrieve lyrics from all sources.")
        return

    if args.out:
        out_path = save_output(lyrics, args.artist, args.title, src)
    else:
        save_output(lyrics, args.artist, args.title, src)

# ---------------------------------------------------------------------
if __name__ == "__main__":
    main()

# end of fetch_best_lyrics.py
