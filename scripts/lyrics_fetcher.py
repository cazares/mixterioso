#!/usr/bin/env python3
import os
import re
import sys
import time
import html
import argparse
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Python LyricsFetcher/1.1"}

def basic_normalize(text: str) -> str:
    """Unescape HTML entities, normalize newlines/whitespace, strip HTML tags if any sneaked in."""
    text = html.unescape(text)
    # strip any accidental angle-tag fragments
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # trim per-line
    text = "\n".join(ln.strip() for ln in text.split("\n"))
    # collapse superfluous blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

def strip_top_noise(text: str, artist: str | None = None, title: str | None = None) -> str:
    """
    Remove leading non-lyric boilerplate seen on many sites:
    - '90 Contributors', 'Translations' + language list, 'Read More', 'Embed'
    - '<Title> Lyrics' / '<Anything> Lyrics'
    - credits/metadata ('Produced by', 'Written by', 'Release Date', 'About', 'Album')
    - early long multi-sentence blurbs
    """
    lines = text.splitlines()
    out = []
    started = False
    in_translations_block = False
    title_lc = (title or "").lower()

    for i, raw in enumerate(lines):
        s = raw.strip()
        low = s.lower()

        if not started:
            # skip empties at the top
            if not s:
                continue

            # explicit junk patterns
            if re.match(r'^\d+\s+contributors?$', low):
                continue

            if low.startswith("translations"):
                in_translations_block = True
                continue

            if in_translations_block:
                # language codes/names often one word or two, short
                if s and len(s) <= 20 and len(s.split()) <= 2:
                    continue
                # end translations block on first longer/multi-word line
                in_translations_block = False
                # re-evaluate the current line below (don’t continue)

            if re.search(r'\bread more\b', low):
                continue
            if re.search(r'\bembed\b', low):
                continue
            if re.search(r'\byou might also like\b', low):
                continue

            if re.search(r'^(about|credits|produced by|written by|release date|album)\b', low):
                continue

            # '<Title> Lyrics' or generic '<something> lyrics'
            if title_lc and re.match(rf'^{re.escape(title_lc)}\s+lyrics$', low):
                continue
            if re.match(r"^[a-z0-9 '\-]+ lyrics$", low):
                continue

            # early long descriptive paragraph (likely not a lyric line)
            if len(s) > 140 and '.' in s:
                continue

            # otherwise: this looks like the first lyric line
            started = True
            out.append(s)
        else:
            out.append(s)

    return "\n".join(out).lstrip("\n")

def remove_inline_annotations(text: str) -> str:
    """Remove bracketed/parenthetical labels like [Chorus], (Verse), etc., then re-trim."""
    # remove standalone annotation lines
    text = re.sub(r'^\s*[\(\[][^\)\]]{1,40}[\)\]]\s*$', '', text, flags=re.M)
    # remove inline short annotations
    text = re.sub(r'[\(\[][^\)\]]{1,40}[\)\]]', '', text)
    # collapse excessive blanks again
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text

def ascii_only(text: str) -> str:
    return text.encode("ascii", errors="ignore").decode("ascii").strip()

def finalize_lyrics(raw: str, artist: str, title: str) -> str:
    """
    Full cleaning pipeline in the right order:
    1) basic normalize (unescape, trim, collapse)
    2) strip top boilerplate (headings/translations/blurbs)
    3) remove annotations
    4) force ASCII-only
    """
    t = basic_normalize(raw)
    t = strip_top_noise(t, artist, title)
    t = remove_inline_annotations(t)
    t = ascii_only(t)
    return t

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
                    return finalize_lyrics(lyr, artist, title)
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
                    # heuristic: first div containing <br> looks like lyrics block
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
                        for seg in c.stripped_strings:
                            lines.append(seg)
                    raw = "\n".join(lines)
                    cleaned = finalize_lyrics(raw, artist, title)
                    if cleaned:
                        return cleaned
        except Exception:
            pass
        time.sleep(delay)
    return None

def fetch_lyrics_genius_api(artist: str, title: str, token: str, retries: int = 3, delay: float = 1.0) -> str | None:
    """Use Genius API to find the canonical song URL, then scrape & clean."""
    hdrs = {**UA, "Authorization": f"Bearer {token}"}
    q = f"{artist} {title}"
    search_url = "https://api.genius.com/search"
    for _ in range(retries):
        try:
            r = requests.get(search_url, headers=hdrs, params={"q": q}, timeout=8)
            if r.status_code == 200:
                data = r.json()
                hits = (data.get("response") or {}).get("hits") or []
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
                                    for seg in c.stripped_strings:
                                        lines.append(seg)
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
    return (
        os.getenv("GENIUS_ACCESS_TOKEN")
        or os.getenv("GENIUS_TOKEN")
        or os.getenv("GENIUS_CLIENT_ACCESS_TOKEN")
    )

def get_lyrics(artist: str, title: str) -> str:
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
        sys.stderr.write(f"[saved] {out_path}\n")
    except Exception as e:
        sys.stderr.write(f"[warn] could not save to {out_path}: {e}\n")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher.py
