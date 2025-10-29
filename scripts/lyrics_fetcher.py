#!/usr/bin/env python3
import os
import re
import sys
import time
import html
import argparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Python LyricsFetcher/1.0"}

def ascii_clean(text: str) -> str:
    # Normalize whitespace, unescape HTML entities, strip non-ascii
    text = html.unescape(text)
    # Remove lingering HTML-like brackets that sometimes slip through
    text = re.sub(r'<[^>]+>', '', text)
    # Normalize newlines
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Collapse trailing spaces on lines
    text = "\n".join([ln.strip() for ln in text.split("\n")])
    # Strip label lines like [Chorus], (Verse)
    text = re.sub(r'[\(\[][^\)\]]{1,40}[\)\]]', '', text)
    # Collapse >2 blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    # ASCII only
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return text.strip()

def slug_simple(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '', s).lower()

def slug_hyphen(s: str) -> str:
    return re.sub(r'\W+', '-', s.strip()).strip('-')

def fetch_lyrics_lyricsovh(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
    url = f"https://api.lyrics.ovh/v1/{requests.utils.requote_uri(artist)}/{requests.utils.requote_uri(title)}"
    for _ in range(retries):
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                data = r.json()
                lyr = data.get("lyrics")
                if lyr and lyr.strip():
                    return ascii_clean(lyr)
        except Exception:
            pass
        time.sleep(delay)
    return None

def fetch_lyrics_azlyrics(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
    artist_slug = slug_simple(artist)
    title_slug  = slug_simple(title)
    url = f"https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html"
    for _ in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                comment = soup.find(string=lambda t: t and "Usage of azlyrics.com content" in t)
                lyrics_div = comment.find_next("div") if comment else None
                if not lyrics_div:
                    # Fallback heuristic: first div containing <br> lines
                    for div in soup.find_all("div"):
                        if div.find("br"):
                            lyrics_div = div
                            break
                if lyrics_div:
                    for br in lyrics_div.find_all("br"):
                        br.replace_with("\n")
                    raw = lyrics_div.get_text("\n")
                    cleaned = ascii_clean(raw)
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        time.sleep(delay)
    return None

def fetch_lyrics_genius_page(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
    # Direct URL guess (no API): https://genius.com/Artist-Title-lyrics
    url = f"https://genius.com/{slug_hyphen(artist)}-{slug_hyphen(title)}-lyrics"
    for _ in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.content, "html.parser")
                containers = soup.select("[data-lyrics-container]")
                if containers:
                    lines = []
                    for c in containers:
                        for seg in c.stripped_strings:
                            lines.append(seg)
                    raw = "\n".join(lines)
                    cleaned = ascii_clean(raw)
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        time.sleep(delay)
    return None

def fetch_lyrics_genius_api(artist: str, title: str, token: str, retries: int = 3, delay: float = 1.0) -> str | None:
    """
    Use Genius API only to find the *correct* song URL, then scrape that page.
    (Official API does not provide lyrics text due to licensing.)
    """
    hdrs = {**UA, "Authorization": f"Bearer {token}"}
    q = f"{artist} {title}"
    search_url = "https://api.genius.com/search"
    for _ in range(retries):
        try:
            r = requests.get(search_url, headers=hdrs, params={"q": q}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                hits = (data.get("response") or {}).get("hits") or []
                # Prefer exact-ish matches on title & primary artist
                def score(hit):
                    res = hit.get("result", {})
                    t = (res.get("title") or "").lower()
                    pa = ((res.get("primary_artist") or {}).get("name") or "").lower()
                    s = 0
                    if slug_simple(title) in slug_simple(t):
                        s += 2
                    if slug_simple(artist) in slug_simple(pa):
                        s += 3
                    return s
                hits_sorted = sorted(hits, key=score, reverse=True)
                for h in hits_sorted[:5]:
                    result = h.get("result", {})
                    url = result.get("url")
                    if not url:
                        continue
                    # Scrape the found URL
                    try:
                        pr = requests.get(url, headers=UA, timeout=10)
                        if pr.status_code == 200:
                            soup = BeautifulSoup(pr.content, "html.parser")
                            containers = soup.select("[data-lyrics-container]")
                            if containers:
                                lines = []
                                for c in containers:
                                    for seg in c.stripped_strings:
                                        lines.append(seg)
                                raw = "\n".join(lines)
                                cleaned = ascii_clean(raw)
                                if cleaned:
                                    return cleaned
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(delay)
    return None

def resolve_genius_token() -> str | None:
    return (
        os.getenv("GENIUS_ACCESS_TOKEN")
        or os.getenv("GENIUS_TOKEN")
        or os.getenv("GENIUS_CLIENT_ACCESS_TOKEN")
    )

def get_lyrics(artist: str, title: str) -> str:
    """
    Fetch lyrics for artist/title. Prefers Genius API (if token is present)
    to get the exact page, then falls back to lyrics.ovh, AZLyrics, and Genius page guess.
    Always returns ASCII-only plain text, or a placeholder on failure.
    """
    # 0) Try Genius API-assisted lookup if token present
    token = resolve_genius_token()
    if token:
        lyr = fetch_lyrics_genius_api(artist, title, token)
        if lyr:
            return lyr

    # 1) Lyrics.ovh
    lyr = fetch_lyrics_lyricsovh(artist, title)
    if lyr:
        return lyr

    # 2) AZLyrics
    lyr = fetch_lyrics_azlyrics(artist, title)
    if lyr:
        return lyr

    # 3) Genius direct page guess
    lyr = fetch_lyrics_genius_page(artist, title)
    if lyr:
        return lyr

    return "Lyrics not found."

def default_outfile(artist: str, title: str) -> str:
    a = slug_simple(artist)
    t = slug_simple(title)
    return f"lyrics_{a}_{t}.txt" if a and t else "lyrics_output.txt"

def main():
    p = argparse.ArgumentParser(description="Fetch clean ASCII song lyrics from multiple sources.")
    p.add_argument("artist", help="Artist name (quoted if contains spaces)")
    p.add_argument("title", help="Song title (quoted if contains spaces)")
    p.add_argument("-o", "--output", help="Output file path (default: auto-named)")
    args = p.parse_args()

    artist = args.artist
    title = args.title
    out_path = args.output or default_outfile(artist, title)

    lyrics = get_lyrics(artist, title)
    print(lyrics)

    try:
        with open(out_path, "w", encoding="ascii", errors="ignore") as f:
            f.write(lyrics + "\n")
        # Tiny UX hint
        sys.stderr.write(f"[saved] {out_path}\n")
    except Exception as e:
        sys.stderr.write(f"[warn] could not save to {out_path}: {e}\n")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher.py
