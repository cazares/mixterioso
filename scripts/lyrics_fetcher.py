#!/usr/bin/env python3
import os, re, sys, time, html
import argparse, requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Python LyricsFetcher/1.1"}

def basic_normalize(text: str) -> str:
    """Unescape HTML entities, normalize newlines/whitespace."""
    text = html.unescape(text)
    text = re.sub(r'<[^>]+>', '', text)  # drop any HTML tags
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = "\n".join(ln.strip() for ln in text.split("\n"))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

def strip_top_noise(text: str, artist: str = "", title: str = "") -> str:
    """Remove leading boilerplate (credits, 'Read More', etc.) commonly found on lyric sites."""
    lines = text.splitlines()
    out = []
    started = False
    in_translations_block = False
    title_lc = title.lower()
    for raw in lines:
        s = raw.strip()
        low = s.lower()
        if not started:
            if not s:  # skip initial empty lines
                continue
            # skip various non-lyric lines
            if re.match(r'^\d+\s+contributors?$', low):  # e.g. "3 contributors"
                continue
            if low.startswith("translations"):
                in_translations_block = True
                continue
            if in_translations_block:
                # skip short language names in translations list
                if s and len(s) <= 20 and len(s.split()) <= 2:
                    continue
                in_translations_block = False  # end translations block
            if re.search(r'\bread more\b', low) or re.search(r'\bembed\b', low) or re.search(r'\byou might also like\b', low):
                continue
            if re.search(r'^(about|credits|produced by|written by|release date|album)\b', low):
                continue
            # Skip lines like "<Title> Lyrics" or any "<something> lyrics"
            if title_lc and re.match(rf'^{re.escape(title_lc)}\s+lyrics$', low):
                continue
            if re.match(r"^[a-z0-9 '\-]+ lyrics$", low):
                continue
            # Skip early long descriptive paragraphs that are likely not lyrics
            if len(s) > 140 and '.' in s:
                continue
            # Otherwise, we've reached the first lyric line
            started = True
            out.append(s)
        else:
            out.append(s)
    return "\n".join(out).lstrip("\n")

def remove_inline_annotations(text: str) -> str:
    """Remove [Chorus] or (Verse) labels and other bracketed annotations."""
    text = re.sub(r'^\s*[\(\[][^\)\]]{1,40}[\)\]]\s*$', '', text, flags=re.M)  # standalone [ ... ] lines
    text = re.sub(r'[\(\[][^\)\]]{1,40}[\)\]]', '', text)  # inline [ ... ] text
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

def ascii_only(text: str) -> str:
    return text.encode("ascii", errors="ignore").decode("ascii").strip()

def finalize_lyrics(raw: str, artist: str, title: str) -> str:
    """Full cleaning pipeline for fetched lyrics text."""
    t = basic_normalize(raw)
    t = strip_top_noise(t, artist, title)
    t = remove_inline_annotations(t)
    t = ascii_only(t)
    return t

def slug_simple(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9]+', '', s).lower()

def slug_hyphen(s: str) -> str:
    return re.sub(r'\W+', '-', s.strip()).strip('-')

# Lyrics source 1: Lyrics.ovh simple API
def fetch_lyrics_lyricsovh(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
    url = f"https://api.lyrics.ovh/v1/{requests.utils.requote_uri(artist)}/{requests.utils.requote_uri(title)}"
    for _ in range(retries):
        try:
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                data = r.json()
                lyr = data.get("lyrics")
                if lyr and lyr.strip():
                    return finalize_lyrics(lyr, artist, title)
        except Exception:
            pass
        time.sleep(delay)
    return None

# Lyrics source 2: Scrape AZLyrics
def fetch_lyrics_azlyrics(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
    artist_slug = slug_simple(artist)
    title_slug  = slug_simple(title)
    url = f"https://www.azlyrics.com/lyrics/{artist_slug}/{title_slug}.html"
    for _ in range(retries):
        try:
            r = requests.get(url, headers=UA, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                # Lyrics are in a div right after a comment that contains a specific phrase
                comment = soup.find(string=lambda t: t and "Usage of azlyrics.com content" in t)
                lyrics_div = comment.find_next("div") if comment else None
                if not lyrics_div:
                    # Fallback: first <div> that has a <br> is likely the lyrics block
                    for div in soup.find_all("div"):
                        if div.find("br"):
                            lyrics_div = div
                            break
                if lyrics_div:
                    for br in lyrics_div.find_all("br"):
                        br.replace_with("\n")
                    raw = lyrics_div.get_text("\n")
                    cleaned = finalize_lyrics(raw, artist, title)
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        time.sleep(delay)
    return None

# Lyrics source 3: Scrape Genius webpage
def fetch_lyrics_genius_page(artist: str, title: str, retries: int = 3, delay: float = 1.0) -> str | None:
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
                        lines += [seg for seg in c.stripped_strings]
                    raw = "\n".join(lines)
                    cleaned = finalize_lyrics(raw, artist, title)
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        time.sleep(delay)
    return None

# Lyrics source 4: Genius API search + scrape (requires Genius API token)
def fetch_lyrics_genius_api(artist: str, title: str, token: str, retries: int = 3, delay: float = 1.0) -> str | None:
    """Use Genius API to find the song URL, then scrape lyrics from it."""
    hdrs = {**UA, "Authorization": f"Bearer {token}"}
    query = f"{artist} {title}"
    search_url = "https://api.genius.com/search"
    for _ in range(retries):
        try:
            r = requests.get(search_url, headers=hdrs, params={"q": query}, timeout=8)
            if r.status_code == 200:
                hits = (r.json().get("response") or {}).get("hits") or []
                # Score results: favor matches on title and artist
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
                    try:
                        pr = requests.get(url, headers=UA, timeout=10)
                        if pr.status_code == 200:
                            soup = BeautifulSoup(pr.content, "html.parser")
                            containers = soup.select("[data-lyrics-container]")
                            if containers:
                                lines = []
                                for c in containers:
                                    lines += [seg for seg in c.stripped_strings]
                                raw = "\n".join(lines)
                                cleaned = finalize_lyrics(raw, artist, title)
                                if cleaned:
                                    return cleaned
                    except Exception:
                        continue
        except Exception:
            pass
        time.sleep(delay)
    return None

def resolve_genius_token() -> str | None:
    # Check env vars for a Genius API token
    return (os.getenv("GENIUS_ACCESS_TOKEN") or os.getenv("GENIUS_TOKEN") or os.getenv("GENIUS_CLIENT_ACCESS_TOKEN"))

def get_lyrics(artist: str, title: str) -> str:
    # Try sources in order
    token = resolve_genius_token()
    if token:
        lyr = fetch_lyrics_genius_api(artist, title, token)
        if lyr:
            return lyr
    lyr = fetch_lyrics_lyricsovh(artist, title)
    if lyr:
        return lyr
    lyr = fetch_lyrics_azlyrics(artist, title)
    if lyr:
        return lyr
    lyr = fetch_lyrics_genius_page(artist, title)
    if lyr:
        return lyr
    return "Lyrics not found."

def default_outfile(artist: str, title: str) -> str:
    a = slug_simple(artist); t = slug_simple(title)
    return f"lyrics_{a}_{t}.txt" if a and t else "lyrics_output.txt"

def main():
    ap = argparse.ArgumentParser(description="Fetch clean ASCII song lyrics from multiple sources.")
    ap.add_argument("artist", help="Artist name")
    ap.add_argument("title", help="Song title")
    ap.add_argument("-o", "--output", help="Output file path (optional)")
    args = ap.parse_args()

    artist = args.artist; title = args.title
    out_path = args.output or default_outfile(artist, title)

    lyrics = get_lyrics(artist, title)
    print(lyrics)  # print lyrics to stdout
    try:
        with open(out_path, "w", encoding="ascii", errors="ignore") as f:
            f.write(lyrics + "\n")
        sys.stderr.write(f"[saved] {out_path}\n")
    except Exception as e:
        sys.stderr.write(f"[warn] could not save to {out_path}: {e}\n")

if __name__ == "__main__":
    main()
