#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_lyrics_fetcher.py

Ultra-fallback lyrics fetcher for English + Mexican/Latin Spanish.

NEW priority (because you’re paying for it 💸):
1. Musixmatch (lyrics)  ← highest priority
2. Musixmatch (subtitles LRC)  ← for karaoke timings, if your plan returns them
3. Musixmatch (richsync)  ← word/segment timings, if available
4. Everything else (Genius, Vagalume public, AudD, KSoft, lyrics.ovh, ChartLyrics, letras.com, lyrics.com, musica.com, YouTube transcript)

Output (strict):

    <title>//by//<artist>

    <clean, merged, normalized lyrics...>

Also:
- prints ALL source outputs, labeled, in a 2-column table
- expands “coro x2” / “chorus x2” as a WHOLE last coro/chorus section
- normalizes/expands Spanish chat slang (“q” → “que”, “xq” → “porque”, “tec” → “etc”)
- capitalizes the start of each line
- won’t crash on Musixmatch subtitle list format
"""

import os
import re
import sys
import argparse
import html as _html
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# ANSI
C_RESET = "\033[0m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_YELLOW = "\033[93m"
C_CYAN = "\033[96m"
C_MAG = "\033[95m"

DOTENV_OK = False
ENV_FILES_TRIED: List[str] = []

# ---------------------------------------------------------------------------
# .env LOADING
# ---------------------------------------------------------------------------
def _manual_load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if val:
                    os.environ.setdefault(key, val)
    except Exception:
        pass


def _normalize_env_aliases() -> None:
    if not os.getenv("GENIUS_ACCESS_TOKEN"):
        alt = os.getenv("GENIUS_TOKEN") or os.getenv("GENIUS_API_TOKEN")
        if alt:
            os.environ["GENIUS_ACCESS_TOKEN"] = alt
    if not os.getenv("YOUTUBE_API_KEY"):
        alt = os.getenv("GOOGLE_API_KEY") or os.getenv("YT_API_KEY")
        if alt:
            os.environ["YOUTUBE_API_KEY"] = alt


def _load_envs() -> None:
    global DOTENV_OK, ENV_FILES_TRIED
    cwd = Path.cwd()
    script_path = Path(__file__).resolve()
    script_dir = script_path.parent
    repo_root = script_dir.parent

    candidates = [
        cwd / ".env",
        cwd / ".env.local",
        script_dir / ".env",
        script_dir / ".env.local",
        repo_root / ".env",
        repo_root / ".env.local",
        Path.home() / ".env",
    ]

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        for p in candidates:
            ENV_FILES_TRIED.append(str(p))
            _manual_load_env_file(p)
        _normalize_env_aliases()
        return

    DOTENV_OK = True
    for p in candidates:
        ENV_FILES_TRIED.append(str(p))
        if p.exists():
            load_dotenv(p)
    _normalize_env_aliases()


_load_envs()

# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    print("This script needs 'requests'. Install with: pip3 install requests", file=sys.stderr)
    sys.exit(1)

USER_AGENT = "auto-lyrics-fetcher/2.1 (karaoke-time + musixmatch-first)"
DEFAULT_TIMEOUT = 12
ENABLE_DEBUG = False
ALLOW_PROMPTS = True


def debug(msg: str):
    if ENABLE_DEBUG:
        print(f"[debug] {msg}", file=sys.stderr)


def http_get(url: str, params=None, headers=None, timeout=DEFAULT_TIMEOUT) -> Optional[requests.Response]:
    h = {"User-Agent": USER_AGENT}
    if headers:
        h.update(headers)
    try:
        resp = requests.get(url, params=params, headers=h, timeout=timeout)
        if resp.status_code == 200:
            return resp
        debug(f"GET {url} -> {resp.status_code}")
    except Exception as e:
        debug(f"http_get error for {url}: {e}")
    return None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def strip_html_tags(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text("\n")
    except Exception:
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<.*?>", "", text)
        return text


def _extract_genius_lyrics_fallback(html: str) -> Optional[str]:
    blocks = re.findall(
        r'<div[^>]+data-lyrics-container="true"[^>]*>(.*?)</div>',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not blocks:
        return None
    parts = []
    for b in blocks:
        b = re.sub(r"<br\s*/?>", "\n", b, flags=re.IGNORECASE)
        b = re.sub(r"<.*?>", "", b)
        b = _html.unescape(b).strip()
        if b:
            parts.append(b)
    return "\n".join(parts).strip() if parts else None


def likely_spanish(text: str) -> bool:
    hits = 0
    for token in (" que ", " porque ", " para ", " tú ", " mí ", " así ", " corazón ", "amor", "dios", "señor"):
        if token in text.lower():
            hits += 1
    return hits >= 1


def normalize_spanish_slang(text: str) -> str:
    slang_map = {
        "xq": "porque",
        "pq": "porque",
        "xk": "porque",
        "q": "que",
        "k": "que",
        "pa": "para",
        "pa'": "para",
        "tmb": "también",
        "tb": "también",
        "ntp": "no te preocupes",
        "tqm": "te quiero mucho",
        "tkm": "te quiero mucho",
        "tec": "etc",
    }
    pattern = r"\b(" + "|".join(re.escape(k) for k in slang_map.keys()) + r")\b"

    def repl(m):
        w = m.group(0)
        low = w.lower()
        rep = slang_map.get(low, w)
        if w[0].isupper():
            rep = rep.capitalize()
        return rep

    return re.sub(pattern, repl, text, flags=re.IGNORECASE)


def clean_lyrics_junk(text: str) -> str:
    if not text:
        return text
    lines = [l.rstrip() for l in text.splitlines()]
    cleaned = []
    skip_prefixes = [
        "Translations", "Traducción", "90 Contributors", "Read More", "Embed",
        "You might also like", "About", "Genius Annotation", "More on Genius",
    ]
    skip_exact = {"", " ", "\u200b"}

    dropping = True
    for line in lines:
        stripped = line.strip()
        if dropping:
            if stripped in skip_exact:
                continue
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            if re.match(r".+ Lyrics$", stripped):
                dropping = False
                continue
            dropping = False
        cleaned.append(line)

    tail_drop_patterns = [
        r"^\d{1,4}Embed$",
        r"^See.*$",
        r"^Report.*$",
        r"^You might also like$",
    ]
    while cleaned and any(re.match(p, cleaned[-1].strip()) for p in tail_drop_patterns):
        cleaned.pop()

    return "\n".join(cleaned).strip()


# ---------------------------------------------------------------------------
# section-aware normalizer (with CORO X2)
# ---------------------------------------------------------------------------
def normalize_sections_and_headers(text: str, title: str, artist: str) -> str:
    if not text:
        return text

    label_regex = re.compile(
        r"^(intro|verso|verse|coro|chorus|pre-chorus|precoro|bridge|puente|outro)\b[:.]?$",
        re.IGNORECASE,
    )
    coro_x2_regex = re.compile(r"^(coro|chorus)\s*x\s*2\b[:.]?$", re.IGNORECASE)

    lines = text.splitlines()

    out_lines: List[str] = []
    current_section_name: Optional[str] = None
    current_section_lines: List[str] = []
    last_coro_lines: List[str] = []

    def _flush_section():
        nonlocal current_section_name, current_section_lines, last_coro_lines
        if current_section_lines:
            out_lines.extend(current_section_lines)
            if current_section_name in ("coro", "chorus"):
                last_coro_lines = current_section_lines[:]
        current_section_lines = []

    for raw_line in lines:
        stripped = raw_line.strip()

        if coro_x2_regex.match(stripped):
            if current_section_lines:
                _flush_section()
            if last_coro_lines:
                out_lines.extend(last_coro_lines)
                out_lines.extend(last_coro_lines)
            continue

        if stripped.lower().startswith("letra de"):
            continue

        m = label_regex.match(stripped)
        if m:
            _flush_section()
            current_section_name = m.group(1).lower()
            continue

        if stripped == "":
            _flush_section()
            if out_lines and out_lines[-1].strip() != "":
                out_lines.append("")
            continue

        if current_section_name is None:
            out_lines.append(raw_line.rstrip())
        else:
            current_section_lines.append(raw_line.rstrip())

    if current_section_lines:
        _flush_section()

    final_lines: List[str] = []
    blank = False
    for l in out_lines:
        if l.strip() == "":
            if blank:
                continue
            blank = True
            final_lines.append("")
        else:
            blank = False
            final_lines.append(l)
    return "\n".join(final_lines).strip()


def de_dupe_lines(text: str) -> str:
    lines = text.splitlines()
    out = []
    prev = None
    for l in lines:
        if l.strip() == "" and (prev is None or prev.strip() == ""):
            prev = l
            continue
        if prev is not None and l.strip() == prev.strip():
            continue
        out.append(l)
        prev = l
    return "\n".join(out)


def _capitalize_each_line(text: str) -> str:
    out = []
    for l in text.splitlines():
        if l.strip():
            out.append(l[:1].upper() + l[1:])
        else:
            out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# env status
# ---------------------------------------------------------------------------
def _mask_val(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 6:
        return "*" * len(v)
    return v[:4] + "..." + v[-3:]


def print_env_status() -> None:
    print(f"{C_CYAN}[env] .env load status:{C_RESET}", file=sys.stderr)
    if not DOTENV_OK:
        print(f"{C_YELLOW}[env] python-dotenv NOT installed; used manual parser instead.{C_RESET}", file=sys.stderr)
    print(f"{C_YELLOW}[env] tried files:{C_RESET}", file=sys.stderr)
    for p in ENV_FILES_TRIED:
        print(f"  - {p}", file=sys.stderr)

    keys = [
        "MUSIXMATCH_API_KEY",
        "GENIUS_ACCESS_TOKEN",
        "VAGALUME_API_KEY",
        "AUDD_API_KEY",
        "KSOFT_API_KEY",
        "YOUTUBE_API_KEY",
    ]
    for k in keys:
        v = os.getenv(k)
        if v:
            print(f"{C_GREEN}[env] {k}: loaded ({_mask_val(v)}){C_RESET}", file=sys.stderr)
        else:
            print(f"{C_RED}[env] {k}: missing{C_RESET}", file=sys.stderr)

    possibles = [x for x in os.environ.keys() if "GENIUS" in x.upper() or "YOUTUBE" in x.upper()]
    if possibles:
        print(f"{C_MAG}[env] other GENIUS/YOUTUBE-like vars I see:{C_RESET}", file=sys.stderr)
        for k in possibles:
            print(f"  {k}={_mask_val(os.getenv(k,''))}", file=sys.stderr)


def get_api_key(name: str, env_var: str) -> str:
    val = os.getenv(env_var, "")
    if val:
        return val
    if not ALLOW_PROMPTS:
        return ""
    try:
        entered = input(f"{env_var} not set. Paste {name} (or leave blank to skip): ").strip()
        return entered
    except EOFError:
        return ""


# ---------------------------------------------------------------------------
# MUSIXMATCH (highest priority)
# ---------------------------------------------------------------------------
def _musixmatch_search_track(artist: str, title: str, key: str) -> Optional[int]:
    search_url = "https://api.musixmatch.com/ws/1.1/track.search"
    params = {
        "q_track": title,
        "q_artist": artist,
        "s_track_rating": "desc",
        "f_has_lyrics": 1,
        "page_size": 5,
        "apikey": key,
    }
    resp = http_get(search_url, params=params)
    if not resp:
        return None
    data = resp.json()
    tracks = data.get("message", {}).get("body", {}).get("track_list", [])
    if not tracks:
        return None
    return tracks[0].get("track", {}).get("track_id")


def fetch_from_musixmatch_lyrics(artist: str, title: str) -> Tuple[Optional[str], dict]:
    api_key = os.getenv("MUSIXMATCH_API_KEY") or get_api_key("Musixmatch API key", "MUSIXMATCH_API_KEY")
    if not api_key:
        return None, {}
    track_id = _musixmatch_search_track(artist, title, api_key)
    if not track_id:
        return None, {}
    lyr_url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    lyr_resp = http_get(lyr_url, params={"track_id": track_id, "apikey": api_key})
    if not lyr_resp:
        return None, {}
    data = lyr_resp.json()
    body = data.get("message", {}).get("body", {}).get("lyrics", {})
    lyrics = body.get("lyrics_body")
    if lyrics:
        lyrics = re.sub(r"\*{3,}.*", "", lyrics, flags=re.DOTALL).strip()
    meta = {
        "track_id": track_id,
        "lyrics_id": body.get("lyrics_id"),
        "language": body.get("lyrics_language"),
        "copyright": body.get("lyrics_copyright"),
        "pixel_tracking_url": body.get("pixel_tracking_url"),
        "script_tracking_url": body.get("script_tracking_url"),
        "restricted": body.get("restricted"),
        "instrumental": body.get("instrumental"),
    }
    print(f"{C_CYAN}[musixmatch] track_id={track_id} lang={meta['language']} instrumental={meta['instrumental']} restricted={meta['restricted']}{C_RESET}", file=sys.stderr)
    if meta.get("copyright"):
        print(f"{C_CYAN}[musixmatch] copyright: {meta['copyright']}{C_RESET}", file=sys.stderr)
    if meta.get("pixel_tracking_url"):
        print(f"{C_YELLOW}[musixmatch] pixel_tracking_url: {meta['pixel_tracking_url']}{C_RESET}", file=sys.stderr)
    if meta.get("script_tracking_url"):
        print(f"{C_YELLOW}[musixmatch] script_tracking_url: {meta['script_tracking_url']}{C_RESET}", file=sys.stderr)
    return lyrics, meta


def fetch_from_musixmatch_subtitles(track_id: int, api_key: str) -> Optional[str]:
    """
    LRC-like timestamps — PERFECT for karaoke.
    Musixmatch sometimes returns:
      { "message": { "body": { "subtitle": {...} } } }
    ...and sometimes:
      { "message": { "body": [ { "subtitle": {...}}, ... ] } }
    Handle both.
    """
    sub_url = "https://api.musixmatch.com/ws/1.1/track.subtitle.get"
    params = {
        "track_id": track_id,
        "apikey": api_key,
        "subtitle_format": "lrc",
        "f_subtitle_length": 999,
        "f_subtitle_length_max_deviation": 999,
    }
    resp = http_get(sub_url, params=params)
    if not resp:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    body = data.get("message", {}).get("body")
    if not body:
        return None

    if isinstance(body, dict):
        sub = body.get("subtitle")
        if sub and isinstance(sub, dict):
            lrc = sub.get("subtitle_body")
            return lrc.strip() if lrc else None

    if isinstance(body, list):
        for item in body:
            if not isinstance(item, dict):
                continue
            sub = item.get("subtitle")
            if sub and isinstance(sub, dict):
                lrc = sub.get("subtitle_body")
                if lrc:
                    return lrc.strip()

    return None


def fetch_from_musixmatch_richsync(track_id: int, api_key: str) -> Optional[str]:
    rich_url = "https://api.musixmatch.com/ws/1.1/track.richsync.get"
    params = {
        "track_id": track_id,
        "apikey": api_key,
        "f_richsync_length": 999,
        "f_richsync_length_max_deviation": 999,
    }
    resp = http_get(rich_url, params=params)
    if not resp:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    sync = data.get("message", {}).get("body", {}).get("richsync")
    if not sync:
        return None
    return str(sync)


# ---------------------------------------------------------------------------
# other providers
# ---------------------------------------------------------------------------
def fetch_from_genius(artist: str, title: str) -> Optional[str]:
    token = os.getenv("GENIUS_ACCESS_TOKEN") or get_api_key("Genius API token", "GENIUS_ACCESS_TOKEN")
    if not token:
        return None
    search_url = "https://api.genius.com/search"
    q = f"{artist} {title}"
    resp = http_get(search_url, params={"q": q}, headers={"Authorization": f"Bearer {token}"})
    if not resp:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    hits = data.get("response", {}).get("hits", [])
    if not hits:
        return None
    song_url = None
    for h in hits:
        res = h.get("result", {})
        prim_artist = res.get("primary_artist", {}).get("name", "")
        if artist.lower() in prim_artist.lower() or prim_artist.lower() in artist.lower():
            song_url = res.get("url")
            break
    if not song_url:
        song_url = hits[0].get("result", {}).get("url")
    if not song_url:
        return None
    page = http_get(song_url)
    if not page:
        return None
    html = page.text
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        lyric_divs = soup.find_all("div", attrs={"data-lyrics-container": "true"})
        if lyric_divs:
            parts = [d.get_text("\n").strip() for d in lyric_divs]
            txt = "\n".join(parts).strip()
        else:
            txt = soup.get_text("\n")
    except Exception:
        txt = _extract_genius_lyrics_fallback(html) or strip_html_tags(html)
    txt = clean_lyrics_junk(txt)
    return txt


def fetch_from_vagalume(artist: str, title: str) -> Optional[str]:
    base_url = "https://api.vagalume.com.br/v1/lyrics"
    artist_slug = requests.utils.requote_uri(artist.strip())
    title_slug = requests.utils.requote_uri(title.strip())
    resp = http_get(f"{base_url}/{artist_slug}/{title_slug}")
    if resp and resp.ok:
        try:
            data = resp.json()
            if "lyrics" in data and data["lyrics"]:
                return str(data["lyrics"]).strip()
            if "text" in data and data["text"]:
                return str(data["text"]).strip()
        except Exception:
            pass
    key = os.getenv("VAGALUME_API_KEY")
    if key:
        url = "https://api.vagalume.com.br/search.php"
        params = {"art": artist, "mus": title, "apikey": key}
        resp2 = http_get(url, params=params)
        if not resp2:
            return None
        try:
            data = resp2.json()
        except Exception:
            return None
        mus = data.get("mus")
        if mus:
            for m in mus:
                if m.get("text"):
                    return m.get("text").strip()
            return mus[0].get("text").strip()
    return None


def fetch_from_audd(artist: str, title: str) -> Optional[str]:
    key = os.getenv("AUDD_API_KEY") or os.getenv("AUDD_API_TOKEN") or get_api_key("AudD API key", "AUDD_API_KEY")
    if not key:
        return None
    url = "https://api.audd.io/findLyrics/"
    params = {"q": f"{artist} {title}", "api_token": key}
    resp = http_get(url, params=params)
    if not resp:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    res = data.get("result")
    if isinstance(res, list) and res:
        best = None
        best_len = 0
        for item in res:
            lyr = item.get("lyrics")
            if lyr and len(lyr) > best_len:
                best = lyr
                best_len = len(lyr)
        return best
    elif isinstance(res, dict):
        return res.get("lyrics")
    return None


def fetch_from_ksoft(artist: str, title: str) -> Optional[str]:
    key = os.getenv("KSOFT_API_KEY") or get_api_key("KSoft API key", "KSOFT_API_KEY")
    if not key:
        return None
    q = f"{title} {artist}"
    url = "https://api.ksoft.si/lyrics/search"
    params = {"q": q, "text_only": "true"}
    headers = {"Authorization": f"Bearer {key}"}
    resp = http_get(url, params=params, headers=headers)
    if not resp:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    results = data.get("data", [])
    if not results:
        return None
    lyr = results[0].get("lyrics")
    return lyr.strip() if lyr else None


def fetch_from_lyricsovh(artist: str, title: str) -> Optional[str]:
    url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
    resp = http_get(url)
    if not resp:
        return None
    try:
        return resp.json().get("lyrics")
    except Exception:
        return None


def fetch_from_chartlyrics(artist: str, title: str) -> Optional[str]:
    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        return None
    qurl = (
        "http://api.chartlyrics.com/apiv1.asmx/SearchLyricDirect"
        f"?Artist={requests.utils.requote_uri(artist)}&Song={requests.utils.requote_uri(title)}"
    )
    resp = http_get(qurl)
    if not resp:
        return None
    try:
        root = ET.fromstring(resp.text)
    except Exception:
        return None
    lyric_elem = root.find(".//Lyric")
    if lyric_elem is not None and lyric_elem.text:
        txt = lyric_elem.text.strip()
        return txt if txt else None
    return None


def scrape_letras_com(artist: str, title: str) -> Optional[str]:
    search_q = f"{artist} {title}".replace(" ", "+")
    search_url = f"https://www.letras.com/?q={search_q}"
    resp = http_get(search_url)
    if not resp:
        return None
    html = resp.text
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        link = soup.select_one("a.song-name") or soup.select_one("a[data-type='song']")
        if not link:
            return None
        song_url = "https://www.letras.com" + link.get("href")
    except Exception:
        m = re.search(r'href="(/[^"]+)"[^>]*class="song-name"', html)
        if not m:
            return None
        song_url = "https://www.letras.com" + m.group(1)
    page = http_get(song_url)
    if not page:
        return None
    page_html = page.text
    try:
        from bs4 import BeautifulSoup  # type: ignore
        psoup = BeautifulSoup(page_html, "html.parser")
        lyric_div = psoup.select_one("div.cnt-letra p") or psoup.select_one("div.lyric-original")
        if lyric_div:
            return lyric_div.get_text("\n").strip()
    except Exception:
        block = re.search(r'<div[^>]+class="cnt-letra"[^>]*>(.*?)</div>', page_html, flags=re.DOTALL | re.IGNORECASE)
        if block:
            text = re.sub(r"<br\s*/?>", "\n", block.group(1))
            text = re.sub(r"<.*?>", "", text)
            return text.strip()
    return None


def scrape_lyrics_com(artist: str, title: str) -> Optional[str]:
    search_url = "https://www.lyrics.com/serp.php"
    params = {"st": title, "qtype": 2, "artist": artist}
    resp = http_get(search_url, params=params)
    if not resp:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("td.tal.qx > strong > a, td.tal.qx a")
    if not link:
        return None
    song_url = "https://www.lyrics.com" + link.get("href")
    page = http_get(song_url)
    if not page:
        return None
    psoup = BeautifulSoup(page.text, "html.parser")
    pre = psoup.find("pre", id="lyric-body-text")
    if pre:
        return pre.get_text("\n").strip()
    return None


def scrape_musica_com(artist: str, title: str) -> Optional[str]:
    search_url = "https://www.musica.com/letras.asp"
    params = {"q": f"{artist} {title}"}
    resp = http_get(search_url, params=params)
    if not resp:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("a.titulo")
    if not link:
        return None
    song_url = link.get("href")
    if not song_url.startswith("http"):
        song_url = "https://www.musica.com/" + song_url.lstrip("/")
    page = http_get(song_url)
    if not page:
        return None
    psoup = BeautifulSoup(page.text, "html.parser")
    lyric_div = psoup.find("div", id="letra")
    if lyric_div:
        return lyric_div.get_text("\n").strip()
    return None


def fetch_from_youtube(artist: str, title: str) -> Optional[str]:
    ykey = os.getenv("YOUTUBE_API_KEY") or get_api_key("YouTube Data API key", "YOUTUBE_API_KEY")
    if not ykey:
        return None
    query = f"{title} {artist} lyrics"
    search_url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "q": query,
        "part": "snippet",
        "maxResults": 5,
        "type": "video",
        "key": ykey,
    }
    resp = http_get(search_url, params=params)
    if not resp:
        return None
    data = resp.json()
    items = data.get("items", [])
    video_id = None
    for item in items:
        vid = item.get("id", {}).get("videoId")
        vtitle = item.get("snippet", {}).get("title", "").lower()
        if not vid:
            continue
        if "lyric" in vtitle or "letra" in vtitle or "official" in vtitle:
            video_id = vid
            break
    if not video_id and items:
        video_id = items[0].get("id", {}).get("videoId")
    if not video_id:
        return None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError:
        return None
    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
    except Exception:
        return None
    transcript_data = None
    for t in transcripts:
        if t.language_code.startswith("es") or t.language_code.startswith("en") or t.is_generated:
            try:
                transcript_data = t.fetch()
                break
            except Exception:
                continue
    if not transcript_data:
        return None
    text_lines = [entry["text"] for entry in transcript_data if entry.get("text")]
    return "\n".join(text_lines).strip() if text_lines else None


# ---------------------------------------------------------------------------
# orchestrator + table
# ---------------------------------------------------------------------------
def fetch_all_sources(artist: str, title: str) -> Dict[str, Optional[str]]:
    mm_lyrics, mm_meta = fetch_from_musixmatch_lyrics(artist, title)
    mm_subs = None
    mm_rich = None
    mm_key = os.getenv("MUSIXMATCH_API_KEY")

    if mm_meta.get("track_id") and mm_key:
        try:
            mm_subs = fetch_from_musixmatch_subtitles(mm_meta["track_id"], mm_key)
            if mm_subs:
                print(f"{C_GREEN}[musixmatch] subtitles (LRC) found — good for karaoke timing{C_RESET}", file=sys.stderr)
            else:
                print(f"{C_YELLOW}[musixmatch] no subtitles/LRC returned for this track_id{C_RESET}", file=sys.stderr)
        except Exception as e:
            print(f"{C_RED}[musixmatch] subtitle fetch failed: {e}{C_RESET}", file=sys.stderr)
        try:
            mm_rich = fetch_from_musixmatch_richsync(mm_meta["track_id"], mm_key)
        except Exception as e:
            print(f"{C_RED}[musixmatch] richsync fetch failed: {e}{C_RESET}", file=sys.stderr)

    return {
        "Musixmatch (lyrics)": mm_lyrics,
        "Musixmatch (subtitles LRC)": mm_subs,
        "Musixmatch (richsync)": mm_rich,
        "Genius": fetch_from_genius(artist, title),
        "Vagalume (public)": fetch_from_vagalume(artist, title),
        "AudD": fetch_from_audd(artist, title),
        "KSoft": fetch_from_ksoft(artist, title),
        "Lyrics.ovh": fetch_from_lyricsovh(artist, title),
        "ChartLyrics": fetch_from_chartlyrics(artist, title),
        "letras.com": scrape_letras_com(artist, title),
        "lyrics.com": scrape_lyrics_com(artist, title),
        "musica.com": scrape_musica_com(artist, title),
        "YouTube transcript": fetch_from_youtube(artist, title),
    }


def _preview(text: Optional[str], width: int = 56) -> str:
    if not text:
        return "(no data)"
    first_line = text.splitlines()[0].strip()
    if len(first_line) > width:
        return first_line[: width - 3] + "..."
    return first_line


def print_sources_table(sources: Dict[str, Optional[str]]) -> None:
    col1_w = 28
    col2_w = 56
    print(f"{C_CYAN}======== RAW SOURCE OUTPUTS ========{C_RESET}", file=sys.stderr)
    print(f"+{'-'*col1_w}+{'-'*col2_w}+", file=sys.stderr)
    print(f"|{'Source':<{col1_w}}|{'Preview':<{col2_w}}|", file=sys.stderr)
    print(f"+{'-'*col1_w}+{'-'*col2_w}+", file=sys.stderr)
    for name, txt in sources.items():
        pv = _preview(txt, width=col2_w)
        print(f"|{name:<{col1_w}}|{pv:<{col2_w}}|", file=sys.stderr)
    print(f"+{'-'*col1_w}+{'-'*col2_w}+", file=sys.stderr)
    print("", file=sys.stderr)


def pick_best_in_order(sources: Dict[str, Optional[str]]) -> Optional[str]:
    best = None
    best_score = 0
    for name, val in sources.items():
        if not val:
            continue
        if name.startswith("Musixmatch (lyrics)"):
            return val
        score = len(re.sub(r"\s+", "", val))
        if score > best_score:
            best_score = score
            best = val
    return best


def merge_candidates_in_order(sources: Dict[str, Optional[str]]) -> str:
    merged_lines: List[str] = []
    seen = set()
    for name, val in sources.items():
        if not val:
            continue
        for line in val.splitlines():
            st = line.strip()
            if not st:
                continue
            if st not in seen:
                merged_lines.append(line.rstrip())
                seen.add(st)
    return "\n".join(merged_lines).strip()


def postprocess_lyrics(lyrics: str, title: str, artist: str, force_spanish: bool = False) -> str:
    lyrics = clean_lyrics_junk(lyrics)
    lyrics = normalize_sections_and_headers(lyrics, title, artist)
    lyrics = de_dupe_lines(lyrics)
    if force_spanish or likely_spanish(lyrics):
        lyrics = normalize_spanish_slang(lyrics)
    lyrics = _capitalize_each_line(lyrics)
    return lyrics.strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Fetch lyrics with Musixmatch first (paid), plus all fallbacks, show sources, expand CORO X2, normalize Spanish slang."
    )
    parser.add_argument("--artist", help="Artist name")
    parser.add_argument("--title", help="Song title")
    parser.add_argument("--lang", default="auto", choices=["auto", "en", "es"], help="Language hint (for slang expansion)")
    parser.add_argument("--merge-strategy", default="merge", choices=["merge", "best"], help="How to combine versions")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--no-prompt", action="store_true", help="Do not prompt for missing API keys")
    args = parser.parse_args()

    global ENABLE_DEBUG, ALLOW_PROMPTS
    ENABLE_DEBUG = args.debug
    if args.no_prompt:
        ALLOW_PROMPTS = False

    print_env_status()

    artist = args.artist or input("Artist: ").strip()
    title = args.title or input("Song title: ").strip()

    print(f"{C_CYAN}[i] Fetching lyrics for '{title}' by '{artist}' (Musixmatch first)...{C_RESET}", file=sys.stderr)

    all_results = fetch_all_sources(artist, title)
    print_sources_table(all_results)

    if not any(v for v in all_results.values()):
        print(f"{title}//by//{artist}\n")
        print(f"{C_RED}[!] No lyrics found from any source.{C_RESET}", file=sys.stderr)
        sys.exit(1)

    if args.merge_strategy == "merge":
        merged = merge_candidates_in_order(all_results)
    else:
        merged = pick_best_in_order(all_results) or merge_candidates_in_order(all_results)

    force_spanish = (args.lang == "es")
    final_lyrics = postprocess_lyrics(merged, title=title, artist=artist, force_spanish=force_spanish)

    # final stdout
    print(f"{title}//by//{artist}\n")
    print(final_lyrics)


if __name__ == "__main__":
    main()

# end of auto_lyrics_fetcher.py
