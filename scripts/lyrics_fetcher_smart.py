#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py

Smart, Spanish-friendly, non-destructive lyrics fetcher for Karaoke Time.

Key features:
- Try several sources (Genius, letras.com, letras.mus.br, musica.com, lyrics.com, rough Musixmatch).
- Clean Spanish texting abbreviations: "q" -> "que", "d" -> "de", ONLY when used by itself.
- Strip structural labels: "Coro:", "CORO:", "chorus", "pre-chorus", "intro", "verse".
- Score candidates by (lines, chars) and PENALIZE "q " variants.
- If an existing lyrics file already exists and is longer → KEEP IT.
- Fallback to existing scripts/lyrics_fetcher.py, but run the same cleanup on its output.
- Only write a placeholder when we truly have nothing AND there was no file before.

Usage (from gen_video.sh):
    python3 scripts/lyrics_fetcher_smart.py "Artist" "Title" -o auto_lyrics/artist-title.txt
"""

import os
import re
import sys
import html
import json
import argparse
import unicodedata
import tempfile
import subprocess
from typing import List, Dict, Optional, Tuple

MIN_OK_CHARS = 60
MIN_OK_LINES = 3

# label-ish lines we want to kill
LABEL_PREFIXES = [
    "coro:", "cor:", "chorus:", "pre-chorus:", "pre chorus:", "intro:",
    "verse:", "verso:", "puente:", "bridge:", "outro:", "refrán:", "refran:",
    "hook:", "estribillo:"
]

# words that often show up in scraped junk
HEADER_JUNK = [
    "translations", "trke", "portugu", "90 contributors",
    "read more", "lyrics powered by", "advertising"
]

def slug_hyphen(s: str) -> str:
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def _fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
      import requests
    except ImportError:
      print("[smart-lyrics] requests not installed; skipping remote fetch:", url)
      return None
    try:
      r = requests.get(url, headers=headers, timeout=10)
      if r.status_code == 200:
        return r.text
    except Exception as e:
      print(f"[smart-lyrics] fetch failed {url}: {e}")
    return None

# ---------------------- CLEANUP HELPERS ---------------------------------
def normalize_newlines(txt: str) -> str:
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    # collapse multiple blank lines
    out_lines = []
    last_blank = False
    for ln in txt.split("\n"):
        s = ln.rstrip()
        if s == "":
            if not last_blank:
                out_lines.append("")
            last_blank = True
        else:
            out_lines.append(s)
            last_blank = False
    return "\n".join(out_lines).strip()

def strip_html_tags(txt: str) -> str:
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"</p>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)
    return txt

def strip_header_junk(txt: str) -> str:
    lines = txt.splitlines()
    cleaned = []
    dropping = True
    for ln in lines:
        low = ln.strip().lower()
        if dropping and (low == "" or any(low.startswith(j) for j in HEADER_JUNK)):
            continue
        dropping = False
        cleaned.append(ln)
    return "\n".join(cleaned).strip()

def is_spanish_like(txt: str) -> bool:
    low = txt.lower()
    return any(w in low for w in [
        " dios", " señor", " espiritu", " espíritu", "corazón", "corazon",
        "jesus", "jesús", "tu amor", "alegrar mi corazon", "que me ama"
    ])

def expand_spanish_sms(txt: str) -> str:
    """
    Replace isolated 'q' or 'Q' with 'que', isolated 'd' with 'de', ONLY when
    they stand as words. We don't touch words like 'quédate', only 'q '.
    """
    # only do this for Spanish-y texts
    if not is_spanish_like(txt):
        return txt
    # ' q ' → ' que '
    txt = re.sub(r"\bq\b", "que", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bd\b", "de", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bxq\b", "porque", txt, flags=re.IGNORECASE)
    return txt

def strip_label_lines(txt: str) -> str:
    """Drop lines like CORO:, chorus:, intro:, etc."""
    out = []
    for ln in txt.splitlines():
        low = ln.strip().lower()
        if any(low.startswith(p) for p in LABEL_PREFIXES):
            # skip it
            continue
        # also drop pure "Coro" w/o :
        if low in ("coro", "coro.", "cor", "chorus", "intro", "bridge", "puente"):
            continue
        out.append(ln)
    return "\n".join(out).strip()

def basic_score(txt: str) -> Tuple[int, int, int]:
    """
    return (lines, chars, penalty)
    penalty grows if there are many SMS 'q ' left
    """
    lines = [l for l in txt.splitlines() if l.strip()]
    chars = len(txt)
    # penalty: remaining q
    penalty = len(re.findall(r"\bq\b", txt, flags=re.IGNORECASE))
    return (len(lines), chars, penalty)

# ---------------------- SOURCE: GENIUS ----------------------------------
def fetch_from_genius(artist: str, title: str) -> Optional[str]:
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    try:
        import requests
        q = f"{artist} {title}"
        r = requests.get(
            "https://api.genius.com/search",
            params={"q": q},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).json()
        hits = r.get("response", {}).get("hits", [])
        if not hits:
            return None
        # pick first matchy-ish
        song_id = hits[0]["result"]["id"]
        sr = requests.get(
            f"https://api.genius.com/songs/{song_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).json()
        path = sr.get("response", {}).get("song", {}).get("path")
        if not path:
            return None
        html_txt = _fetch_url("https://genius.com" + path)
        if not html_txt:
            return None
        blocks = re.findall(
            r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>',
            html_txt,
            flags=re.DOTALL,
        )
        if not blocks:
            return None
        parts = []
        for b in blocks:
            b = strip_html_tags(b)
            parts.append(b.strip())
        return "\n".join(parts).strip()
    except Exception as e:
        print("[smart-lyrics] genius failed:", e)
        return None

# ---------------------- SOURCE: letras.com / letras.mus.br --------------
def fetch_from_letras_any(artist: str, title: str) -> Optional[str]:
    """
    Try letras.com (the one you pasted) first, then letras.mus.br.
    """
    query = f"{artist} {title}".strip()
    # letras.com style search
    search_url_1 = "https://www.letras.com/?q=" + query.replace(" ", "+")
    html_txt = _fetch_url(search_url_1, headers={"User-Agent": "Mozilla/5.0"})
    if html_txt:
        m = re.search(r'href="(/[^"]+/[^"/]+/)"', html_txt)
        if m:
            song_url = "https://www.letras.com" + m.group(1)
            song_html = _fetch_url(song_url, headers={"User-Agent": "Mozilla/5.0"})
            if song_html:
                m2 = re.search(r'<div class="cnt-letra p402_premium">(.*?)</div>', song_html, flags=re.DOTALL)
                if not m2:
                    m2 = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
                if m2:
                    block = m2.group(1)
                    block = strip_html_tags(block)
                    return block.strip()

    # letras.mus.br fallback
    search_url_2 = "https://www.letras.mus.br/?q=" + query.replace(" ", "+")
    html_txt2 = _fetch_url(search_url_2, headers={"User-Agent": "Mozilla/5.0"})
    if html_txt2:
        m = re.search(r'href="(/[^"]+/[^"/]+/)"', html_txt2)
        if m:
            song_url = "https://www.letras.mus.br" + m.group(1)
            song_html = _fetch_url(song_url, headers={"User-Agent": "Mozilla/5.0"})
            if song_html:
                m2 = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
                if m2:
                    block = m2.group(1)
                    block = strip_html_tags(block)
                    return block.strip()

    return None

# ---------------------- SOURCE: musica.com ------------------------------
def fetch_from_musica_com(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}".strip()
    search_url = "https://www.musica.com/letras.asp?q=" + q.replace(" ", "+")
    html_txt = _fetch_url(search_url)
    if not html_txt:
        return None
    m = re.search(r'href="(letras\.asp\?letra=[0-9]+)"', html_txt)
    if not m:
        return None
    song_url = "https://www.musica.com/" + m.group(1)
    song_html = _fetch_url(song_url)
    if not song_html:
        return None
    m = re.search(r'<div id="letra">(.*?)</div>', song_html, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = strip_html_tags(block)
    return block.strip()

# ---------------------- SOURCE: lyrics.com (weak) -----------------------
def fetch_from_lyrics_com(artist: str, title: str) -> Optional[str]:
    artist_slug = slug_hyphen(artist)
    title_slug = slug_hyphen(title)
    url = f"https://www.lyrics.com/lyric/{artist_slug}/{title_slug}"
    html_txt = _fetch_url(url)
    if not html_txt:
        return None
    m = re.search(r'<pre[^>]*id="lyric-body-text"[^>]*>(.*?)</pre>', html_txt, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = strip_html_tags(block)
    return block.strip()

# ---------------------- SOURCE: Musixmatch-ish (HTML) -------------------
def fetch_from_musixmatch(artist: str, title: str) -> Optional[str]:
    """
    Musixmatch often shields, but let's try the same URL pattern Miguel pasted.
    If it fails, just return None.
    """
    slug_artist = artist.replace(" ", "-")
    slug_title = title.replace(" ", "-")
    # attempt Spanish localized URL
    url = f"https://www.musixmatch.com/es/letras/{slug_artist}/{slug_title}"
    html_txt = _fetch_url(url, headers={"User-Agent": "Mozilla/5.0"})
    if not html_txt:
        return None
    # Musixmatch HTML is noisy; grab <p>...</p> in main lyrics area
    # We'll just grab the biggest <p> block
    ps = re.findall(r"<p[^>]*>(.*?)</p>", html_txt, flags=re.DOTALL)
    if not ps:
        return None
    # pick the longest
    ps = [strip_html_tags(p).strip() for p in ps]
    ps.sort(key=len, reverse=True)
    candidate = ps[0]
    return candidate.strip() or None

# ---------------------- LEGACY FALLBACK ---------------------------------
def try_legacy(artist: str, title: str, scripts_dir: str) -> Optional[str]:
    legacy_path = os.path.join(scripts_dir, "lyrics_fetcher.py")
    if not os.path.isfile(legacy_path):
        return None
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="legacy_lyrics_", suffix=".txt")
    os.close(tmp_fd)
    cmd = [sys.executable, legacy_path, artist, title, "-o", tmp_path]
    print("[smart-lyrics] trying legacy fetcher:", " ".join(cmd))
    subprocess.run(cmd, check=False)
    if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 20:
        with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
            txt = f.read()
        os.unlink(tmp_path)
        return txt
    return None

# ---------------------- PICK BEST ---------------------------------------
def clean_and_score(label: str, raw: str) -> Optional[Dict]:
    if not raw:
        return None
    txt = raw
    txt = strip_html_tags(txt)
    txt = normalize_newlines(txt)
    txt = strip_header_junk(txt)
    txt = strip_label_lines(txt)
    txt = expand_spanish_sms(txt)
    txt = normalize_newlines(txt)
    lines, chars, penalty = basic_score(txt)
    return {
        "source": label,
        "text": txt,
        "lines": lines,
        "chars": chars,
        "penalty": penalty,
    }

def pick_best_candidate(cands: List[Dict]) -> Optional[Dict]:
    if not cands:
        return None
    # sort by: lines desc, chars desc, penalty asc
    cands.sort(key=lambda c: (c["lines"], c["chars"], -c["penalty"]), reverse=True)
    # but we reversed, so penalty asc → we'll just do 2-step
    # easier: sort by lines desc, chars desc, then filter high-penalty
    cands.sort(key=lambda c: (-c["lines"], -c["chars"], c["penalty"]))
    return cands[0]

# ---------------------- MAIN --------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("-o", "--output", help="output lyrics txt")
    args = ap.parse_args()

    artist = args.artist.strip()
    title = args.title.strip()
    out_path = args.output or f"{slug_hyphen(artist)}-{slug_hyphen(title)}.txt"
    scripts_dir = os.path.dirname(os.path.realpath(__file__))

    print(f"[smart-lyrics] fetching for: {artist!r} – {title!r}")

    candidates: List[Dict] = []

    g = fetch_from_genius(artist, title)
    if g:
        c = clean_and_score("genius", g)
        if c: candidates.append(c)

    l = fetch_from_letras_any(artist, title)
    if l:
        c = clean_and_score("letras", l)
        if c: candidates.append(c)

    m = fetch_from_musica_com(artist, title)
    if m:
        c = clean_and_score("musica.com", m)
        if c: candidates.append(c)

    mm = fetch_from_musixmatch(artist, title)
    if mm:
        c = clean_and_score("musixmatch", mm)
        if c: candidates.append(c)

    lc = fetch_from_lyrics_com(artist, title)
    if lc:
        c = clean_and_score("lyrics.com", lc)
        if c: candidates.append(c)

    # legacy fallback
    if not candidates or all(c["chars"] < MIN_OK_CHARS for c in candidates):
        legacy_txt = try_legacy(artist, title, scripts_dir)
        if legacy_txt:
            c = clean_and_score("legacy_lyrics_fetcher.py", legacy_txt)
            if c:
                candidates.append(c)

    best = pick_best_candidate(candidates) if candidates else None

    # if there's an existing file, and it's longer → KEEP IT
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
            existing = f.read().strip()
        ex_clean = clean_and_score("existing", existing)
        if ex_clean:
            if not best:
                print("[smart-lyrics] no better lyrics found; keeping existing file.")
                return
            # compare
            if (ex_clean["lines"], ex_clean["chars"]) > (best["lines"], best["chars"]):
                print("[smart-lyrics] existing file is longer/more complete — keeping it.")
                return

    if best and best["chars"] >= MIN_OK_CHARS and best["lines"] >= MIN_OK_LINES:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(best["text"].strip() + "\n")
        print(f"[smart-lyrics] picked {best['source']} ({best['lines']} lines, {best['chars']} chars)")
        print(f"[smart-lyrics] wrote to {out_path}")
    else:
        # only write placeholder if there was no file
        if not os.path.exists(out_path):
            placeholder = f"{title}\n{artist}\n\n[lyrics not found — add manually]\n"
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(placeholder)
            print(f"[smart-lyrics] no lyrics found; wrote placeholder to {out_path}")
        else:
            print("[smart-lyrics] no good lyrics found; keeping existing file.")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher_smart.py
