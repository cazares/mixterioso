#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lyrics_fetcher_smart.py

Smart, Spanish-friendly lyrics fetcher for Karaoke Time.

- tries: built-in known-good, Genius, letras.com / letras.mus.br, musica.com, (very rough) Musixmatch, lyrics.com
- if requests is NOT installed, will try `curl` to fetch HTML
- fallback to legacy scripts/lyrics_fetcher.py
- cleans texting Spanish: q -> que, d -> de, xq -> porque
- strips label lines: Coro:, Chorus:, Intro:, Puente:, etc.
- keeps existing file if it is longer

Special case: Jesus Adrian Romero – “Me Dice Que Me Ama”
→ we return the long version Miguel pasted.
"""

import os
import re
import sys
import html
import argparse
import unicodedata
import tempfile
import subprocess
from typing import List, Dict, Optional, Tuple

MIN_OK_CHARS = 60
MIN_OK_LINES = 3

LABEL_PREFIXES = [
    "coro:", "cor:", "chorus:", "pre-chorus:", "pre chorus:", "intro:",
    "verse:", "verso:", "puente:", "bridge:", "outro:", "refrán:", "refran:",
    "hook:", "estribillo:"
]

HEADER_JUNK = [
    "translations", "trke", "portugu", "90 contributors",
    "read more", "lyrics powered by", "advertising"
]

# ---------------------------------------------------------------------
# 1. known-good lyrics bank (exact songs that gave us trouble)
# ---------------------------------------------------------------------
KNOWN_GOOD_LYRICS: Dict[Tuple[str, str], str] = {
    # normalize to lower, no accents
    ("jesus adrian romero", "me dice que me ama"): """Me dice que me ama cuando escucho llover
Me dice que me ama con un atardecer
Lo dice sin palabras con las olas del mar
Lo dice en la mañana con mi respirar

Me dice que me ama y que conmigo quiere estar
Me dice que me busca cuando salgo yo a pasear
Que ha hecho lo que existe para llamar mi atención
Que quiere conquistarme y alegrar mi corazón

Me dice que me ama y que conmigo quiere estar
Me dice que me busca cuando salgo yo a pasear
Que ha hecho lo que existe para llamar mi atención
Que quiere conquistarme y alegrar mi corazón

Me dice que me ama cuando veo la cruz
Sus manos extendidas
Así tan grande es su amor
Lo dicen las heridas de sus manos y pies
Me dice que me ama una y otra vez

Me dice que me ama y que conmigo quiere estar
Me dice que me busca cuando salgo yo a pasear
Que ha hecho lo que existe para llamar mi atención
Que quiere conquistarme y alegrar mi corazón

Me dice que me ama y que conmigo quiere estar
Me dice que me busca cuando salgo yo a pasear
Que ha hecho lo que existe para llamar mi atención
Que quiere conquistarme y alegrar mi corazón
"""
}

# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def slug_hyphen(s: str) -> str:
    s = s.strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s

def _try_import_requests():
    try:
        import requests  # type: ignore
        return requests
    except Exception:
        return None

REQUESTS = _try_import_requests()

def _fetch_url_via_curl(url: str) -> Optional[str]:
    """Fallback fetcher when requests isn't installed."""
    try:
        out = subprocess.check_output(
            ["curl", "-L", "-s", "-m", "10", "-A", "Mozilla/5.0", url],
            stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", errors="ignore")
    except Exception:
        return None

def _fetch_url(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    if REQUESTS is not None:
        try:
            r = REQUESTS.get(url, headers=headers or {"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
    # fallback to curl
    return _fetch_url_via_curl(url)

def normalize_newlines(txt: str) -> str:
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
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
        " dios", " señor", " jesus", " jesús", "corazon", "corazón",
        "que me ama", "alegrar mi corazon", "atención", "atencion"
    ])

def expand_spanish_sms(txt: str) -> str:
    if not is_spanish_like(txt):
        return txt
    # q -> que
    txt = re.sub(r"\bq\b", "que", txt, flags=re.IGNORECASE)
    # d -> de
    txt = re.sub(r"\bd\b", "de", txt, flags=re.IGNORECASE)
    # xq -> porque
    txt = re.sub(r"\bxq\b", "porque", txt, flags=re.IGNORECASE)
    return txt

def strip_label_lines(txt: str) -> str:
    out = []
    for ln in txt.splitlines():
        low = ln.strip().lower()
        if any(low.startswith(p) for p in LABEL_PREFIXES):
            continue
        if low in ("coro", "coro.", "cor", "chorus", "intro", "puente", "bridge"):
            continue
        out.append(ln)
    return "\n".join(out).strip()

def fix_common_mojibake(txt: str) -> str:
    # your legacy lyrics had "maсana" (cyrillic c) and "atenciуn"
    txt = txt.replace("maсana", "mañana")
    txt = txt.replace("atenciуn", "atención")
    txt = txt.replace("asн", "así")
    txt = txt.replace("d sus", "de sus")
    return txt

def basic_score(txt: str) -> Tuple[int, int, int]:
    lines = [l for l in txt.splitlines() if l.strip()]
    chars = len(txt)
    penalty = len(re.findall(r"\bq\b", txt, flags=re.IGNORECASE))
    return (len(lines), chars, penalty)

# ---------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------
def fetch_from_genius(artist: str, title: str) -> Optional[str]:
    token = os.getenv("GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    try:
        import requests  # type: ignore
    except Exception:
        return None
    try:
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
            parts.append(strip_html_tags(b).strip())
        return "\n".join(parts).strip()
    except Exception as e:
        print("[smart-lyrics] genius failed:", e)
        return None

def fetch_from_letras_any(artist: str, title: str) -> Optional[str]:
    query = f"{artist} {title}".strip()
    search_url_1 = "https://www.letras.com/?q=" + query.replace(" ", "+")
    html_txt = _fetch_url(search_url_1)
    if html_txt:
        m = re.search(r'href="(/[^"]+/[^"/]+/)"', html_txt)
        if m:
            song_url = "https://www.letras.com" + m.group(1)
            song_html = _fetch_url(song_url)
            if song_html:
                m2 = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
                if m2:
                    block = strip_html_tags(m2.group(1))
                    return block.strip()
    # letras.mus.br
    search_url_2 = "https://www.letras.mus.br/?q=" + query.replace(" ", "+")
    html_txt2 = _fetch_url(search_url_2)
    if html_txt2:
        m = re.search(r'href="(/[^"]+/[^"/]+/)"', html_txt2)
        if m:
            song_url = "https://www.letras.mus.br" + m.group(1)
            song_html = _fetch_url(song_url)
            if song_html:
                m2 = re.search(r'<div class="cnt-letra[^"]*">(.*?)</div>', song_html, flags=re.DOTALL)
                if m2:
                    block = strip_html_tags(m2.group(1))
                    return block.strip()
    return None

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
    block = strip_html_tags(m.group(1))
    return block.strip()

def fetch_from_musixmatch(artist: str, title: str) -> Optional[str]:
    slug_artist = artist.replace(" ", "-")
    slug_title = title.replace(" ", "-")
    url = f"https://www.musixmatch.com/es/letras/{slug_artist}/{slug_title}"
    html_txt = _fetch_url(url)
    if not html_txt:
        return None
    ps = re.findall(r"<p[^>]*>(.*?)</p>", html_txt, flags=re.DOTALL)
    if not ps:
        return None
    ps = [strip_html_tags(p).strip() for p in ps]
    ps.sort(key=len, reverse=True)
    return ps[0].strip() if ps else None

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
    return strip_html_tags(m.group(1)).strip()

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

def clean_and_score(label: str, raw: str) -> Optional[Dict]:
    if not raw:
        return None
    txt = raw
    txt = strip_html_tags(txt)
    txt = normalize_newlines(txt)
    txt = strip_header_junk(txt)
    txt = strip_label_lines(txt)
    txt = expand_spanish_sms(txt)
    txt = fix_common_mojibake(txt)
    txt = normalize_newlines(txt)
    lines, chars, penalty = basic_score(txt)
    return {
        "source": label,
        "text": txt,
        "lines": lines,
        "chars": chars,
        "penalty": penalty,
    }

def pick_best(cands: List[Dict]) -> Optional[Dict]:
    if not cands:
        return None
    # prefer more lines, then more chars, then lower penalty
    cands.sort(key=lambda c: (-c["lines"], -c["chars"], c["penalty"]))
    return cands[0]

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

    # 0) try known-good bank
    key = (unicodedata.normalize("NFKD", artist).encode("ascii", "ignore").decode("ascii").lower().strip(),
           unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower().strip())
    candidates: List[Dict] = []
    if key in KNOWN_GOOD_LYRICS:
        print("[smart-lyrics] using built-in known-good lyrics for this song.")
        c = clean_and_score("built-in", KNOWN_GOOD_LYRICS[key])
        if c:
            candidates.append(c)

    # 1) web sources
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

    # 2) legacy as last resort
    if not candidates or all(c["chars"] < MIN_OK_CHARS for c in candidates):
        legacy = try_legacy(artist, title, scripts_dir)
        if legacy:
            c = clean_and_score("legacy_lyrics_fetcher.py", legacy)
            if c: candidates.append(c)

    best = pick_best(candidates) if candidates else None

    # 3) existing local file wins if it's longer
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8", errors="ignore") as f:
            ex_txt = f.read().strip()
        ex_c = clean_and_score("existing", ex_txt)
        if ex_c:
            if not best or (ex_c["lines"], ex_c["chars"]) > (best["lines"], best["chars"]):
                print("[smart-lyrics] existing file is longer/more complete — keeping it.")
                return

    if best and best["chars"] >= MIN_OK_CHARS and best["lines"] >= MIN_OK_LINES:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(best["text"].strip() + "\n")
        print(f"[smart-lyrics] picked {best['source']} ({best['lines']} lines, {best['chars']} chars)")
        print(f"[smart-lyrics] wrote to {out_path}")
    else:
        if not os.path.exists(out_path):
            placeholder = f"{title}\n{artist}\n\n[lyrics not found — add manually]\n"
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(placeholder)
            print(f"[smart-lyrics] no lyrics found; wrote placeholder to {out_path}")
        else:
            print("[smart-lyrics] no better lyrics found; keeping existing file.")

if __name__ == "__main__":
    main()
# end of lyrics_fetcher_smart.py
