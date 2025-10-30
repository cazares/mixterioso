#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py — multi-source lyric fetcher + best-version chooser.

Usage:
  python3 scripts/lyrics_fetcher_smart.py "Artist" "Title" -o auto_lyrics/artist-title.txt

Sources tried (in order):
  1) Genius API (if GENIUS_ACCESS_TOKEN is set) → scrape lyrics page
  2) Genius HTML search (no API key)           → scrape lyrics page
  3) Letras.com search                          → scrape lyrics page
  4) Lyrics.com search                          → scrape lyrics page

Selection heuristic:
  - Clean each candidate (strip site junk, collapse blanks, expand Spanish texting)
  - Score = (lines_count * 200) + char_count
  - Pick highest score

Spanish texting fixes (conservative):
  - \bq\b           → que
  - \bxq\b          → porque
  - \bpa\b          → para       (common but safe)

Notes:
  - We *preserve* accents and punctuation.
  - We try to keep section tags like [Coro] as-is (good for your pipeline).
  - If no source returns anything, we write a small placeholder.

Env:
  - GENIUS_ACCESS_TOKEN (optional)

Dependencies:
  - requests, beautifulsoup4 (auto-installed if missing)
"""
import os, sys, re, json, time, html, unicodedata
from typing import Optional, List, Dict, Tuple

# ---------- Tiny bootstrap installer ----------
def _ensure(pkg: str, import_name: Optional[str] = None):
    name = import_name or pkg
    try:
        __import__(name)
        return
    except Exception:
        import subprocess
        print(f"[deps] installing {pkg} …")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        __import__(name)

_ensure("requests")
_ensure("beautifulsoup4", "bs4")

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) KaraokeTime/1.0 Safari/537.36"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "es,en;q=0.9"})

# ---------- Helpers ----------
def deaccent(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def norm_for_match(s: str) -> str:
    return re.sub(r"\s+", " ", deaccent(s.lower())).strip()

def http_get(url: str, **kw) -> Optional[requests.Response]:
    try:
        r = SESSION.get(url, timeout=kw.pop("timeout", 12), **kw)
        if r.status_code == 200 and r.text and len(r.text) > 200:
            return r
    except Exception:
        return None
    return None

def best_match_contains(a: str, b: str) -> bool:
    """accent-insensitive, space-collapsed containment"""
    return norm_for_match(a) in norm_for_match(b) or norm_for_match(b) in norm_for_match(a)

# ---------- Cleaning / Normalization ----------
JUNK_PATTERNS = [
    r"^\s*\d+\s+Contributors\s*$",
    r"^\s*Translations\s*$",
    r"^\s*Embed\s*$",
    r"^\s*You might also like\s*$",
    r"^\s*Read\s+More\s*$",
    r"^\s*Genius\s*$",
    r"^\s*Paroles\s*$",
    r"^\s*Traducci[oó]n\s*$",
    r"^\s*Lyrics\s*$",
    r"^\s*Add\s+lyrics\s*$",
    r"^\s*Report\s+[\w\s]+$",
]

JUNK_RE = re.compile("|".join(JUNK_PATTERNS), re.IGNORECASE)

def expand_spanish_texting(line: str) -> str:
    # Conservative replacements; word boundaries only.
    line = re.sub(r"\bq\b", "que", line, flags=re.IGNORECASE)
    line = re.sub(r"\bxq\b", "porque", line, flags=re.IGNORECASE)
    line = re.sub(r"\bpa\b", "para", line, flags=re.IGNORECASE)
    return line

def clean_lyrics_text(raw: str) -> str:
    # Unescape & normalize line endings
    txt = html.unescape(raw)
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")

    lines = [l.strip() for l in txt.split("\n")]

    cleaned: List[str] = []
    for l in lines:
        if not l:
            cleaned.append("")  # keep structure; we'll collapse later
            continue
        # Remove obvious site junk headers
        if JUNK_RE.search(l):
            continue
        # Kill super-long prose blurbs that are clearly metadata/descriptions
        if len(l) > 220 and l.count(" ") > 25:
            continue
        # Expand texting
        l2 = expand_spanish_texting(l)
        cleaned.append(l2)

    # Collapse multiple blank lines to a single blank
    out: List[str] = []
    prev_blank = False
    for l in cleaned:
        if l == "":
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(l)
            prev_blank = False

    # Strip leading/trailing blank lines
    while out and out[0] == "":
        out.pop(0)
    while out and out[-1] == "":
        out.pop()

    return "\n".join(out)

def score_text(txt: str) -> Tuple[int, int, int]:
    """Return (score, lines, chars). Higher is better."""
    lines = [l for l in txt.split("\n")]
    nonempty = [l for l in lines if l.strip()]
    char_count = sum(len(l) for l in nonempty)
    line_count = len(nonempty)
    score = line_count * 200 + char_count
    return score, line_count, char_count

# ---------- Genius (API + HTML) ----------
def fetch_from_genius_api(artist: str, title: str) -> Optional[str]:
    token = os.environ.get("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    q = f"{artist} {title}".strip()
    url = "https://api.genius.com/search"
    try:
        r = SESSION.get(url, params={"q": q}, headers={"Authorization": f"Bearer {token}"}, timeout=12)
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception:
        return None

    hits = data.get("response", {}).get("hits", []) or []
    # Pick the best hit where artist/title roughly match
    page_url = None
    for h in hits:
        res = h.get("result", {})
        full_title = res.get("full_title") or ""
        prim = res.get("primary_artist", {}).get("name", "")
        if best_match_contains(artist, prim) and (best_match_contains(title, full_title) or best_match_contains(title, res.get("title",""))):
            page_url = res.get("url")
            break
    if not page_url and hits:
        page_url = hits[0].get("result", {}).get("url")

    if not page_url:
        return None
    return fetch_from_genius_page(page_url)

def fetch_from_genius_page(url: str) -> Optional[str]:
    r = http_get(url)
    if not r: 
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    # Newer Genius pages: lyrics chunks in <div data-lyrics-container="true">
    blocks = soup.select('div[data-lyrics-container="true"]')
    if not blocks:
        # Older fallback: try class name patterns
        blk = soup.select_one(".lyrics") or soup.select_one("div.Lyrics__Root")
        if blk:
            raw = blk.get_text("\n", strip=False)
            return raw
        return None
    parts: List[str] = []
    for b in blocks:
        # Keep line breaks; <br> → \n
        text = b.get_text("\n", strip=False)
        parts.append(text)
    return "\n".join(parts).strip()

def fetch_from_genius_html_search(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}".strip()
    url = "https://genius.com/search"
    r = http_get(url, params={"q": q})
    if not r:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    # Result cards may vary; look for first anchor pointing to /Artist-title-lyrics
    cand = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("https://genius.com/") and href.endswith("-lyrics"):
            # Try to sanity-check artist/title containment on the anchor text or surrounding text
            label = a.get_text(" ", strip=True)
            if best_match_contains(artist, label) or best_match_contains(title, label):
                cand = href
                break
    if not cand:
        # take first plausible lyrics link
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("https://genius.com/") and "lyrics" in href:
                cand = href; break
    if not cand:
        return None
    return fetch_from_genius_page(cand)

# ---------- Letras.com ----------
def fetch_from_letras(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}".strip()
    # Search
    sr = http_get("https://www.letras.com/busca/", params={"q": q})
    if not sr:
        return None
    ssoup = BeautifulSoup(sr.text, "html.parser")
    link = None
    for a in ssoup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True)
        if "/letras/" in href or "/artista/" in href or "/musica/" in href or href.startswith("/"):
            # Try to find a candidate where link text contains artist or title
            if best_match_contains(artist, txt) or best_match_contains(title, txt):
                link = href
                break
    if not link:
        return None
    if link.startswith("/"):
        link = "https://www.letras.com" + link
    pr = http_get(link)
    if not pr:
        return None
    psoup = BeautifulSoup(pr.text, "html.parser")
    # Letras often uses div with class 'cnt-letra p402_premium' or variations
    holder = psoup.select_one("div.cnt-letra") or psoup.select_one("div.letra") or psoup.select_one("article")
    if not holder:
        return None
    # Replace <br> with newlines when extracting
    text = holder.get_text("\n", strip=False)
    return text.strip() if text.strip() else None

# ---------- Lyrics.com ----------
def fetch_from_lyrics_dot_com(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}".strip()
    sr = http_get("https://www.lyrics.com/lyrics/" + requests.utils.quote(q))
    if not sr:
        return None
    ssoup = BeautifulSoup(sr.text, "html.parser")
    link = None
    # Results are links to /lyric/<id>/<slug>
    for a in ssoup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/lyric/"):
            label = a.get_text(" ", strip=True)
            # Prefer something that mentions title
            if best_match_contains(title, label) or best_match_contains(artist, label):
                link = "https://www.lyrics.com" + href
                break
    if not link:
        return None
    pr = http_get(link)
    if not pr:
        return None
    psoup = BeautifulSoup(pr.text, "html.parser")
    holder = psoup.select_one("#lyric-body-text") or psoup.select_one(".lyric-body")
    if not holder:
        return None
    text = holder.get_text("\n", strip=False)
    return text.strip() if text.strip() else None

# ---------- Orchestrator ----------
def fetch_candidates(artist: str, title: str) -> List[Tuple[str, str]]:
    """Return list of (source_label, raw_text)"""
    cand: List[Tuple[str, str]] = []
    # 1) Genius API
    try:
        raw = fetch_from_genius_api(artist, title)
        if raw: cand.append(("genius_api", raw))
    except Exception:
        pass
    # 2) Genius HTML
    try:
        raw = fetch_from_genius_html_search(artist, title)
        if raw: cand.append(("genius_html", raw))
    except Exception:
        pass
    # 3) Letras
    try:
        raw = fetch_from_letras(artist, title)
        if raw: cand.append(("letras", raw))
    except Exception:
        pass
    # 4) Lyrics.com
    try:
        raw = fetch_from_lyrics_dot_com(artist, title)
        if raw: cand.append(("lyrics.com", raw))
    except Exception:
        pass
    return cand

def choose_best(candidates: List[Tuple[str, str]]) -> Tuple[str, str, Dict]:
    """
    Return (source_label, cleaned_text, debug_meta)
    """
    best_label = ""
    best_clean = ""
    best_score = -1
    meta = {"candidates": []}

    for label, raw in candidates:
        cleaned = clean_lyrics_text(raw)
        score, lines, chars = score_text(cleaned)
        meta["candidates"].append({
            "source": label,
            "score": score,
            "lines": lines,
            "chars": chars
        })
        if score > best_score:
            best_score = score
            best_label = label
            best_clean = cleaned

    return best_label, best_clean, meta

# ---------- CLI ----------
def main():
    import argparse, pathlib
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("-o", "--out", help="Output .txt path", required=True)
    ap.add_argument("--dump-debug-json", help="If set, write candidate scores JSON next to output", action="store_true")
    args = ap.parse_args()

    artist, title = args.artist.strip(), args.title.strip()
    print(f">>> [lyrics] fetching candidates for: {artist} — \"{title}\"")

    cands = fetch_candidates(artist, title)
    if not cands:
        print("[WARN] No sources returned lyrics; writing placeholder.")
        placeholder = f"{title}\n\n[Letra no encontrada]\n"
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(placeholder)
        print(f"[saved] {args.out}")
        return

    label, best, meta = choose_best(cands)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(best)
    print(f"[OK] best source: {label} — lines={len(best.splitlines())}, chars={len(best)}")
    print(f"[saved] {args.out}")

    if args.dump_debug_json:
        dbg_path = os.path.splitext(args.out)[0] + ".lyrics_candidates.json"
        with open(dbg_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[debug] wrote {dbg_path}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[interrupt] bye")
# end of lyrics_fetcher_smart.py
