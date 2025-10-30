#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py

Safer, non-destructive lyrics fetcher.

- Try multiple online sources.
- Clean + score.
- If nothing decent is found, FALL BACK to existing scripts/lyrics_fetcher.py.
- If STILL nothing, DO NOT overwrite an existing lyrics file.
- Only write placeholder if there was no file to begin with.

This is made to be called by gen_video.sh like:
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

# ---------------------------------------------------------------------
# small utils
# ---------------------------------------------------------------------
JUNK_PREFIXES = [
    "Translations", "Trke", "Türkçe", "Português", "Portugus",
    "Français", "90 Contributors", "36 Contributors", "Read More",
    "You might also like", "About", "Lyrics for", "Embed",
    "Click here", "See also", "Official Video", "Lyrics powered by",
    "ADVERTISING"
]

SPANISH_SHORTCUTS = {
    r"\bq\b": "que",
    r"\bxq\b": "porque",
    r"\bpa\b": "para",
}

MIN_OK_CHARS = 60      # "real song" threshold (tweak)
MIN_OK_LINES = 3

def slug_hyphen(s: str) -> str:
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def is_spanish_text(txt: str) -> bool:
    txt_low = txt.lower()
    for w in (" que ", " dios", " señor", " corazon", " corazón", " espiritu", " espíritu", "jesus", "jesús"):
        if w in txt_low:
            return True
    return False

def fix_spanish_sms_shortcuts(txt: str) -> str:
    if not is_spanish_text(txt):
        return txt
    for pat, repl in SPANISH_SHORTCUTS.items():
        txt = re.sub(pat, repl, txt, flags=re.IGNORECASE)
    return txt

def normalize_lines(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in raw.split("\n")]
    out = []
    last_blank = False
    for ln in lines:
        if ln.strip() == "":
            if not last_blank:
                out.append("")
            last_blank = True
        else:
            out.append(ln)
            last_blank = False
    return "\n".join(out).strip()

def strip_junk_header(text: str) -> str:
    lines = text.splitlines()
    final = []
    dropping = True
    for ln in lines:
        ln_stripped = ln.strip()
        if dropping and (ln_stripped == "" or any(ln_stripped.startswith(p) for p in JUNK_PREFIXES)):
            continue
        dropping = False
        final.append(ln)
    return "\n".join(final).strip()

def score_lyrics(txt: str) -> Tuple[int, int]:
    lines = [l for l in txt.splitlines() if l.strip()]
    return (len(lines), len(txt))

def fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    try:
        import requests
    except ImportError:
        print("[lyrics_fetcher_smart] requests not installed; skipping remote sources.")
        return None
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"[lyrics_fetcher_smart] fetch failed for {url}: {e}")
    return None

# ---------------------------------------------------------------------
# source: Genius (API + HTML)
# ---------------------------------------------------------------------
def fetch_from_genius_api(artist: str, title: str) -> Optional[str]:
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    q = f"{artist} {title}"
    try:
        import requests
        search = requests.get(
            "https://api.genius.com/search",
            params={"q": q},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).json()
        hits = search.get("response", {}).get("hits", [])
        if not hits:
            return None

        song_id = None
        for h in hits:
            full_title = h["result"].get("full_title", "").lower()
            if title.split()[0].lower() in full_title:
                song_id = h["result"]["id"]
                break
        if not song_id:
            song_id = hits[0]["result"]["id"]

        song_obj = requests.get(
            f"https://api.genius.com/songs/{song_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).json()
        path = song_obj.get("response", {}).get("song", {}).get("path")
        if not path:
            return None
        html_text = fetch_url("https://genius.com" + path)
        if not html_text:
            return None

        # extract blocks
        blocks = re.findall(
            r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>',
            html_text,
            flags=re.DOTALL,
        )
        if not blocks:
            return None
        out_parts = []
        for b in blocks:
            b = re.sub(r"<[^>]+>", "", b)
            b = html.unescape(b)
            out_parts.append(b.strip())
        return "\n".join(out_parts).strip()
    except Exception as e:
        print(f"[lyrics_fetcher_smart] Genius failed: {e}")
        return None

# ---------------------------------------------------------------------
# source: letras.mus.br
# ---------------------------------------------------------------------
def fetch_from_letras(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}"
    search_url = "https://www.letras.mus.br/?q=" + q.replace(" ", "+")
    html_text = fetch_url(search_url, headers={"User-Agent": "Mozilla/5.0"})
    if not html_text:
        return None
    # try to find first song link
    m = re.search(r'href="(/[^"]+/[^"]+/)"', html_text)
    if not m:
        return None
    song_url = "https://www.letras.mus.br" + m.group(1)
    song_html = fetch_url(song_url, headers={"User-Agent": "Mozilla/5.0"})
    if not song_html:
        return None
    m = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = re.sub(r"<br\s*/?>", "\n", block)
    block = re.sub(r"</p>", "\n", block)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ---------------------------------------------------------------------
# source: musica.com
# ---------------------------------------------------------------------
def fetch_from_musica_com(artist: str, title: str) -> Optional[str]:
    q = f"{artist} {title}"
    search_url = "https://www.musica.com/letras.asp?q=" + q.replace(" ", "+")
    html_text = fetch_url(search_url)
    if not html_text:
        return None
    m = re.search(r'href="(letras\.asp\?letra=[0-9]+)"', html_text)
    if not m:
        return None
    song_url = "https://www.musica.com/" + m.group(1)
    song_html = fetch_url(song_url)
    if not song_html:
        return None
    m = re.search(r'<div id="letra">(.*?)</div>', song_html, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = re.sub(r"<br\s*/?>", "\n", block)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ---------------------------------------------------------------------
# source: lyrics.com (weak fallback)
# ---------------------------------------------------------------------
def fetch_from_lyrics_com(artist: str, title: str) -> Optional[str]:
    artist_slug = slug_hyphen(artist)
    title_slug = slug_hyphen(title)
    url = f"https://www.lyrics.com/lyric/{artist_slug}/{title_slug}"
    html_text = fetch_url(url)
    if not html_text:
        return None
    m = re.search(r'<pre[^>]*id="lyric-body-text"[^>]*>(.*?)</pre>', html_text, flags=re.DOTALL)
    if not m:
        return None
    block = m.group(1)
    block = re.sub(r"<[^>]+>", "", block)
    block = html.unescape(block)
    return block.strip()

# ---------------------------------------------------------------------
# fallback to existing lyrics_fetcher.py
# ---------------------------------------------------------------------
def try_legacy_fetcher(artist: str, title: str, final_out: str, scripts_dir: str) -> Optional[str]:
    legacy_path = os.path.join(scripts_dir, "lyrics_fetcher.py")
    if not os.path.isfile(legacy_path):
        return None
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="legacy_lyrics_", suffix=".txt")
    os.close(tmp_fd)
    try:
        cmd = [sys.executable, legacy_path, artist, title, "-o", tmp_path]
        print(f"[lyrics_fetcher_smart] trying legacy fetcher: {' '.join(cmd)}")
        subprocess.run(cmd, check=False)
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 30:
            with open(tmp_path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read().strip()
            os.unlink(tmp_path)
            return txt
    except Exception as e:
        print(f"[lyrics_fetcher_smart] legacy fetcher failed: {e}")
    return None

# ---------------------------------------------------------------------
# pick best
# ---------------------------------------------------------------------
def pick_best(variants: List[Tuple[str, str]]) -> Optional[Dict]:
    cleaned = []
    for src, raw in variants:
        if not raw:
            continue
        txt = normalize_lines(raw)
        txt = strip_junk_header(txt)
        txt = fix_spanish_sms_shortcuts(txt)
        line_count, char_count = score_lyrics(txt)
        cleaned.append({
            "source": src,
            "text": txt,
            "lines": line_count,
            "chars": char_count,
        })
    if not cleaned:
        return None
    cleaned.sort(key=lambda d: (d["lines"], d["chars"]), reverse=True)
    return cleaned[0], cleaned

# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("artist")
    ap.add_argument("title")
    ap.add_argument("-o", "--output", help="output txt path")
    args = ap.parse_args()

    artist = args.artist.strip()
    title = args.title.strip()

    # figure out scripts dir to call legacy
    scripts_dir = os.path.dirname(os.path.realpath(__file__))

    print(f"[smart-lyrics] fetching for: {artist!r} – {title!r}")

    variants: List[Tuple[str, str]] = []

    # remote sources
    g = fetch_from_genius_api(artist, title)
    if g:
        variants.append(("genius_api", g))

    l = fetch_from_letras(artist, title)
    if l:
        variants.append(("letras", l))

    m = fetch_from_musica_com(artist, title)
    if m:
        variants.append(("musica_com", m))

    lc = fetch_from_lyrics_com(artist, title)
    if lc:
        variants.append(("lyrics_com", lc))

    best = None
    ranked = []
    if variants:
        best, ranked = pick_best(variants) or (None, [])

    # if we don't have a good-enough one, call legacy
    good_enough = False
    if best:
        if best["chars"] >= MIN_OK_CHARS and best["lines"] >= MIN_OK_LINES:
            good_enough = True

    if not good_enough:
        legacy_txt = try_legacy_fetcher(artist, title, args.output or "", scripts_dir)
        if legacy_txt:
            # clean legacy too
            legacy_txt = normalize_lines(legacy_txt)
            legacy_txt = strip_junk_header(legacy_txt)
            legacy_txt = fix_spanish_sms_shortcuts(legacy_txt)
            lc_lines, lc_chars = score_lyrics(legacy_txt)
            if not best or (lc_lines, lc_chars) > (best["lines"], best["chars"]):
                best = {
                    "source": "legacy_lyrics_fetcher.py",
                    "text": legacy_txt,
                    "lines": lc_lines,
                    "chars": lc_chars,
                }
                good_enough = lc_chars >= MIN_OK_CHARS and lc_lines >= MIN_OK_LINES

    out_path = args.output or f"{slug_hyphen(artist)}-{slug_hyphen(title)}.txt"

    # final write logic (NON-DESTRUCTIVE)
    if best and best["text"].strip():
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(best["text"].strip() + "\n")
        print(f"[smart-lyrics] picked: {best['source']} ({best['lines']} lines, {best['chars']} chars)")
        print(f"[smart-lyrics] wrote to {out_path}")
    else:
        # nothing good
        if os.path.exists(out_path):
            # we DO NOT overwrite
            print(f"[smart-lyrics] no good lyrics found; keeping existing file: {out_path}")
        else:
            # ok, no file existed, write placeholder
            placeholder = f"{title}\n{artist}\n\n[lyrics not found — add manually]\n"
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(placeholder)
            print(f"[smart-lyrics] no lyrics found; wrote placeholder to {out_path}")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher_smart.py
