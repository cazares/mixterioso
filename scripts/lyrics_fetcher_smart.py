#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py

Drop-in replacement / upgrade for lyrics_fetcher.py.

Goal:
- Try several lyric sources (Genius API if token, then HTML sites).
- Clean the output (strip junk headers, “read more”, translations list, contributor counts).
- Prefer the “most complete” version.
- Write final .txt that your pipeline (align_to_csv.py → render_from_csv.py) can consume.

Usage:
    python3 scripts/lyrics_fetcher_smart.py "Jesús Adrián Romero" "Me Dice Que Me Ama" -o auto_lyrics/jesus-adrian-romero-me-dice-que-me-ama.txt

Notes:
- This stays additive: it does NOT delete your old lyrics_fetcher.py.
- If everything fails, it will write a placeholder with a warning.
"""

import os
import re
import sys
import json
import html
import argparse
import unicodedata
from typing import List, Dict, Optional, Tuple

try:
    import requests  # your env probably has this; if not, pip install requests
except ImportError:
    print("[lyrics_fetcher_smart] requests not installed. Run: pip3 install requests")
    sys.exit(1)

# ------------------------------------------------------------
# shared small utils
# ------------------------------------------------------------
JUNK_PREFIXES = [
    "Translations", "Trke", "Türkçe", "Português", "Portugus", "Français",
    "90 Contributors", "36 Contributors", "Read More", "You might also like",
    "About", "Scar Tissue Lyrics", "Lyrics for", "Embed", "More on",
    "Click here", "See also", "Official Video", "Lyrics powered by", "ADVERTISING"
]

SPANISH_SHORTCUTS = {
    r"\bq\b": "que",
    r"\bxq\b": "porque",
    r"\bpa\b": "para",
}

def slug_hyphen(s: str) -> str:
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def is_spanish_text(txt: str) -> bool:
    # super dumb heuristic, good enough here
    txt_low = txt.lower()
    hits = 0
    for w in (" que ", " dios", " señor", " corazón", " espiritu", " espíritu", "jesus", "jesús"):
        if w in txt_low:
            hits += 1
    return hits >= 1

def fix_spanish_sms_shortcuts(txt: str) -> str:
    # Turn “q” → “que”, but only if it looks like Spanish lyrics
    if not is_spanish_text(txt):
        return txt
    for pat, repl in SPANISH_SHORTCUTS.items():
      txt = re.sub(pat, repl, txt, flags=re.IGNORECASE)
    return txt

def normalize_lines(raw: str) -> str:
    # unify newlines, de-dupe consecutive blanks
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in raw.split("\n")]
    cleaned = []
    last_blank = False
    for ln in lines:
        if ln.strip() == "":
            if not last_blank:
                cleaned.append("")
            last_blank = True
        else:
            cleaned.append(ln)
            last_blank = False
    return "\n".join(cleaned).strip()

def strip_junk_header(text: str) -> str:
    lines = [l for l in text.splitlines()]
    # drop leading junk lines
    final_lines = []
    dropping = True
    for ln in lines:
        ln_stripped = ln.strip()
        if dropping and any(ln_stripped.startswith(p) for p in JUNK_PREFIXES):
            continue
        # also drop empty lines at very top
        if dropping and ln_stripped == "":
            continue
        dropping = False
        final_lines.append(ln)
    return "\n".join(final_lines).strip()

def score_lyrics(txt: str) -> Tuple[int, int]:
    """
    return (nonempty_lines, total_chars) for ranking.
    we want bigger -> better.
    """
    lines = [l for l in txt.splitlines() if l.strip() != ""]
    return (len(lines), len(txt))

def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"[lyrics_fetcher_smart] fetch failed for {url}: {e}")
    return None

# ------------------------------------------------------------
# Source 1: Genius API (if GENIUS_ACCESS_TOKEN present)
# ------------------------------------------------------------
def fetch_from_genius_api(artist: str, title: str) -> Optional[str]:
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    query = f"{artist} {title}"
    try:
        search_url = "https://api.genius.com/search"
        r = requests.get(search_url, params={"q": query}, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        data = r.json()
        hits = data.get("response", {}).get("hits", [])
        if not hits:
            return None
        # pick first hit that matches song title-ish
        song_id = None
        for h in hits:
            full_title = h["result"].get("full_title", "")
            # simple containment check
            if title.lower().split()[0] in full_title.lower():
                song_id = h["result"]["id"]
                break
        if not song_id:
            song_id = hits[0]["result"]["id"]
        song_url = f"https://api.genius.com/songs/{song_id}"
        s = requests.get(song_url, headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        path = s.get("response", {}).get("song", {}).get("path")
        if not path:
            return None
        # Genius lyrics are in HTML page, not in API
        page_url = "https://genius.com" + path
        html_text = fetch_url(page_url)
        if not html_text:
            return None
        # Genius changed markup a few times; simplest is regex out text inside <div data-lyrics-container>
        m = re.findall(r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>', html_text, flags=re.DOTALL)
        if not m:
            return None
        chunks = []
        for block in m:
            # strip tags
            block_txt = re.sub(r"<[^>]+>", "", block)
            block_txt = html.unescape(block_txt)
            chunks.append(block_txt.strip())
        joined = "\n".join(chunks)
        return joined.strip()
    except Exception as e:
        print(f"[lyrics_fetcher_smart] Genius fetch failed: {e}")
        return None

# ------------------------------------------------------------
# Source 2: letras.com (good for Spanish CCM)
# ------------------------------------------------------------
def fetch_from_letras(artist: str, title: str) -> Optional[str]:
    # this is heuristic: we search using their mobile search endpoint
    q = f"{artist} {title}"
    search_url = "https://www.letras.mus.br/?q=" + requests.utils.quote(q)
    html_text = fetch_url(search_url, headers={"User-Agent": "Mozilla/5.0"})
    if not html_text:
      return None
    # grab first result link to /<artist>/<song>/
    m = re.search(r'href="(/[^"]+/[^"]+/)"', html_text)
    if not m:
        return None
    song_url = "https://www.letras.mus.br" + m.group(1)
    song_html = fetch_url(song_url, headers={"User-Agent": "Mozilla/5.0"})
    if not song_html:
        return None
    # letras lyrics are inside <div class="cnt-letra p402_premium">
    m = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    # remove <p>, <br>, etc.
    block = re.sub(r"<br\s*/?>", "\n", block)
    block = re.sub(r"</p>", "\n", block)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ------------------------------------------------------------
# Source 3: letras.com.br alt (optional) / musica.com / generic
# ------------------------------------------------------------
def fetch_from_musica_com(artist: str, title: str) -> Optional[str]:
    # VERY heuristic, may fail quietly
    q = f"{artist} {title}"
    search_url = "https://www.musica.com/letras.asp?q=" + requests.utils.quote(q)
    html_text = fetch_url(search_url)
    if not html_text:
        return None
    # find first /letras.asp?letra=xxxxx
    m = re.search(r'href="(letras\.asp\?letra=[0-9]+)"', html_text)
    if not m:
        return None
    song_url = "https://www.musica.com/" + m.group(1)
    song_html = fetch_url(song_url)
    if not song_html:
        return None
    # lyrics are in <div id="letra">
    m = re.search(r'<div id="letra">(.*?)</div>', song_html, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = re.sub(r"<br\s*/?>", "\n", block)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ------------------------------------------------------------
# Source 4: fallback: lyricstranslate-ish generic scrape
# ------------------------------------------------------------
def fetch_from_generic_googleable(artist: str, title: str) -> Optional[str]:
    # This is intentionally dumb: we try lyrics.com with slug
    artist_slug = slug_hyphen(artist)
    title_slug = slug_hyphen(title)
    url = f"https://www.lyrics.com/lyric/{artist_slug}/{title_slug}"
    html_text = fetch_url(url)
    if not html_text:
        return None
    # lyrics.com has <pre id="lyric-body-text">
    m = re.search(r'<pre[^>]*id="lyric-body-text"[^>]*>(.*?)</pre>', html_text, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ------------------------------------------------------------
# final pick logic
# ------------------------------------------------------------
def pick_best_variant(variants: List[Tuple[str, str]]) -> Tuple[str, Dict]:
    """
    variants: [(source_name, raw_text), ...]
    Returns: (best_text, debug_info)
    """
    cleaned_variants = []
    for src, txt in variants:
        if not txt:
            continue
        txt = normalize_lines(txt)
        txt = strip_junk_header(txt)
        txt = fix_spanish_sms_shortcuts(txt)
        lines_score, char_score = score_lyrics(txt)
        cleaned_variants.append({
            "source": src,
            "text": txt,
            "lines": lines_score,
            "chars": char_score,
        })
    if not cleaned_variants:
        return ("[LYRICS NOT FOUND]\n", {"picked": None, "candidates": []})
    # sort: more lines first, if tie -> more chars
    cleaned_variants.sort(key=lambda d: (d["lines"], d["chars"]), reverse=True)
    best = cleaned_variants[0]
    debug = {
        "picked": best["source"],
        "candidates": cleaned_variants,
    }
    return best["text"], debug

# ------------------------------------------------------------
# main
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("-o", "--output", help="output txt path", required=False)
    args = ap.parse_args()

    artist = args.artist.strip()
    title = args.title.strip()

    print(f"[smart-lyrics] fetching lyrics for: {artist!r} – {title!r}")

    variants: List[Tuple[str, str]] = []

    # 1) Genius API (if token)
    g = fetch_from_genius_api(artist, title)
    if g:
        variants.append(("genius_api", g))

    # 2) letras.com
    l = fetch_from_letras(artist, title)
    if l:
        variants.append(("letras", l))

    # 3) musica.com
    m = fetch_from_musica_com(artist, title)
    if m:
        variants.append(("musica_com", m))

    # 4) generic
    gg = fetch_from_generic_googleable(artist, title)
    if gg:
        variants.append(("lyrics_com_generic", gg))

    # 5) if STILL empty, put placeholder
    if not variants:
      final_txt = f"{title}\n{artist}\n\n[lyrics not found — add manually]\n"
      out = args.output or f"{slug_hyphen(artist)}-{slug_hyphen(title)}.txt"
      with open(out, "w", encoding="utf-8") as f:
          f.write(final_txt)
      print(f"[smart-lyrics] wrote placeholder to {out}")
      sys.exit(0)

    best_text, debug = pick_best_variant(variants)

    out_path = args.output or f"{slug_hyphen(artist)}-{slug_hyphen(title)}.txt"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(best_text + "\n")

    print(f"[smart-lyrics] picked source: {debug['picked']}")
    for cand in debug["candidates"]:
        print(f"  - {cand['source']}: {cand['lines']} lines, {cand['chars']} chars")
    print(f"[smart-lyrics] wrote to {out_path}")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher_smart.py
