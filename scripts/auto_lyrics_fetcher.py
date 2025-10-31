#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_lyrics_fetcher.py

Ultra-fallback lyrics fetcher for English + Mexican/Latin Spanish.

Output format (IMPORTANT):

    <title>//by//<artist>

    <clean, merged, normalized lyrics...>

No extra headers.

Features:
- tries multiple APIs (Genius, Musixmatch, Vagalume, AudD, KSoft, Lyrics.ovh, ChartLyrics)
- then scrapes (letras.com, lyrics.com, musica.com)
- optional YouTube captions
- merges/dedupes
- strips “Letra de … de …”, “intro”, “verse”, “coro”, etc.
- expands Spanish/Mx SMS (q→que, xq→porque, pa→para, tmb→también, tec→etc)
- NEW: lines like “CORO X2” / “coro x2” / “chorus x2” → repeat the previous stanza twice
"""

import os
import re
import sys
import argparse
from typing import List, Dict, Optional

try:
    import requests
except ImportError:
    print("This script needs 'requests'. Install with: pip3 install requests")
    sys.exit(1)

USER_AGENT = "auto-lyrics-fetcher/1.2 (karaoke-time)"
DEFAULT_TIMEOUT = 12
ENABLE_DEBUG = False
ALLOW_PROMPTS = True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

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


def strip_html_tags(html: str) -> str:
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text("\n")
    except Exception:
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
        text = re.sub(r"<.*?>", "", text)
        return text


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
        r"^You might also like$"
    ]
    while cleaned and any(re.match(p, cleaned[-1].strip()) for p in tail_drop_patterns):
        cleaned.pop()

    return "\n".join(cleaned).strip()


def normalize_sections_and_headers(text: str, title: str, artist: str) -> str:
    """
    - drop 'Letra de ... de ...'
    - drop generic labels (intro, verse, coro, pre-chorus, bridge...)
    - BUT: if line is 'coro x2' / 'chorus x2' (any case, with optional space), we
      repeat the *previous stanza* twice (for sing-along)
    """
    if not text:
        return text

    section_labels = {
        "intro", "intro:", "verso", "verso:", "verse", "verse:",
        "coro", "coro:", "chorus", "chorus:", "pre-chorus", "pre-chorus:",
        "precoro", "precoro:", "bridge", "bridge:", "puente", "puente:",
        "outro", "outro:"
    }

    lines = text.splitlines()
    out_lines: List[str] = []
    current_stanza: List[str] = []  # lines since last blank

    coro_x2_pattern = re.compile(r"^(coro|chorus)\s*x\s*2\b[:.]?$", re.IGNORECASE)

    for raw_line in lines:
        stripped = raw_line.strip()

        # 1) special case: CORO X2 / chorus x2
        if coro_x2_pattern.match(stripped):
            if current_stanza:
                # repeat the stanza twice
                out_lines.extend(current_stanza)
                out_lines.extend(current_stanza)
            # do not output the label itself
            continue

        # 2) drop "Letra de ..."
        if stripped.lower().startswith("letra de"):
            continue

        # 3) drop simple labels
        if stripped.lower() in section_labels or stripped.lower().replace("-", " ") in section_labels:
            continue

        # 4) otherwise, keep line
        out_lines.append(raw_line)

        # maintain stanza buffer
        if stripped == "":
            current_stanza = []
        else:
            current_stanza.append(raw_line)

    # collapse extra blanks
    final_lines: List[str] = []
    blank_counter = 0
    for l in out_lines:
        if l.strip() == "":
            blank_counter += 1
            if blank_counter > 1:
                continue
        else:
            blank_counter = 0
        final_lines.append(l.rstrip())

    return "\n".join(final_lines).strip()


def de_dupe_lines(text: str) -> str:
    seen = set()
    out_lines = []
    for line in text.splitlines():
        key = line.strip()
        if key not in seen:
            out_lines.append(line)
            seen.add(key)
    return "\n".join(out_lines)


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
# providers (APIs)
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
    txt = strip_html_tags(html)
    txt = clean_lyrics_junk(txt)
    return txt


def fetch_from_musixmatch(artist: str, title: str) -> Optional[str]:
    key = os.getenv("MUSIXMATCH_API_KEY") or get_api_key("Musixmatch API key", "MUSIXMATCH_API_KEY")
    if not key:
        return None
    search_url = "https://api.musixmatch.com/ws/1.1/track.search"
    params = {
        "q_track": title,
        "q_artist": artist,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": key,
    }
    resp = http_get(search_url, params=params)
    if not resp:
        return None
    data = resp.json()
    tracks = data.get("message", {}).get("body", {}).get("track_list", [])
    if not tracks:
        return None
    track_id = tracks[0].get("track", {}).get("track_id")
    if not track_id:
        return None

    lyr_url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    lyr_resp = http_get(lyr_url, params={"track_id": track_id, "apikey": key})
    if not lyr_resp:
        return None
    lyr_data = lyr_resp.json()
    lyrics = lyr_data.get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body")
    if not lyrics:
        return None
    lyrics = re.sub(r"\*{3,}.*", "", lyrics, flags=re.DOTALL).strip()
    return lyrics


def fetch_from_vagalume(artist: str, title: str) -> Optional[str]:
    key = os.getenv("VAGALUME_API_KEY") or get_api_key("Vagalume API key", "VAGALUME_API_KEY")
    url = "https://api.vagalume.com.br/search.php"
    params = {"art": artist, "mus": title}
    if key:
        params["apikey"] = key
    resp = http_get(url, params=params)
    if not resp:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    mus = data.get("mus")
    if not mus:
        return None
    for m in mus:
        if m.get("text"):
            return m.get("text")
    return mus[0].get("text")


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


# ---------------------------------------------------------------------------
# scrapers
# ---------------------------------------------------------------------------

def scrape_letras_com(artist: str, title: str) -> Optional[str]:
    search_q = f"{artist} {title}".replace(" ", "+")
    search_url = f"https://www.letras.com/?q={search_q}"
    resp = http_get(search_url)
    if not resp:
        return None
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.select_one("a.song-name") or soup.select_one("a[data-type='song']")
    if not link:
        return None
    song_url = "https://www.letras.com" + link.get("href")
    page = http_get(song_url)
    if not page:
        return None
    psoup = BeautifulSoup(page.text, "html.parser")
    lyric_div = psoup.select_one("div.cnt-letra p") or psoup.select_one("div.lyric-original")
    if lyric_div:
        return lyric_div.get_text("\n").strip()
    all_ps = psoup.select("div.cnt-letra p")
    if all_ps:
        return "\n".join([p.get_text("\n") for p in all_ps]).strip()
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


# ---------------------------------------------------------------------------
# youtube (optional)
# ---------------------------------------------------------------------------

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
# orchestrator
# ---------------------------------------------------------------------------

def fetch_all_sources(artist: str, title: str) -> Dict[str, Optional[str]]:
    return {
        "musixmatch": fetch_from_musixmatch(artist, title),
        "genius": fetch_from_genius(artist, title),
        "vagalume": fetch_from_vagalume(artist, title),
        "audd": fetch_from_audd(artist, title),
        "ksoft": fetch_from_ksoft(artist, title),
        "lyricsovh": fetch_from_lyricsovh(artist, title),
        "chartlyrics": fetch_from_chartlyrics(artist, title),
        "letras.com": scrape_letras_com(artist, title),
        "lyrics.com": scrape_lyrics_com(artist, title),
        "musica.com": scrape_musica_com(artist, title),
        "youtube": fetch_from_youtube(artist, title),
    }


def pick_best(candidates: List[str]) -> Optional[str]:
    best = None
    best_score = 0
    for c in candidates:
        if not c:
            continue
        score = len(re.sub(r"\s+", "", c))
        if score > best_score:
            best_score = score
            best = c
    return best


def merge_candidates(candidates: List[str]) -> str:
    base = pick_best(candidates) or ""
    base_lines = [l.rstrip() for l in base.splitlines()]
    seen = set(l.strip() for l in base_lines if l.strip())
    merged_lines = list(base_lines)

    for cand in candidates:
        if not cand or cand == base:
            continue
        for line in cand.splitlines():
            st = line.strip()
            if not st:
                continue
            if st not in seen:
                merged_lines.append(line)
                seen.add(st)

    return "\n".join(merged_lines).strip()


def postprocess_lyrics(lyrics: str, title: str, artist: str, force_spanish: bool = False) -> str:
    lyrics = clean_lyrics_junk(lyrics)
    lyrics = normalize_sections_and_headers(lyrics, title, artist)
    lyrics = de_dupe_lines(lyrics)
    if force_spanish or likely_spanish(lyrics):
        lyrics = normalize_spanish_slang(lyrics)
    return lyrics.strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch lyrics from many sources, merge, clean, repeat CORO X2, normalize Spanish slang."
    )
    parser.add_argument("--artist", help="Artist name")
    parser.add_argument("--title", help="Song title")
    parser.add_argument("--lang", default="auto", choices=["auto", "en", "es"], help="Language hint")
    parser.add_argument("--merge-strategy", default="merge", choices=["merge", "best"], help="How to combine versions")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument("--no-prompt", action="store_true", help="Do not prompt for missing API keys")
    args = parser.parse_args()

    global ENABLE_DEBUG, ALLOW_PROMPTS
    ENABLE_DEBUG = args.debug
    if args.no_prompt:
        ALLOW_PROMPTS = False

    artist = args.artist or input("Artist: ").strip()
    title = args.title or input("Song title: ").strip()

    print(f"[i] Fetching lyrics for '{title}' by '{artist}' ...", file=sys.stderr)

    all_results = fetch_all_sources(artist, title)
    candidates = [txt for txt in all_results.values() if txt]

    if not candidates:
        print(f"{title}//by//{artist}\n")
        print("[!] No lyrics found from any source.")
        print("Tip: set GENIUS_ACCESS_TOKEN, MUSIXMATCH_API_KEY, AUDD_API_KEY, VAGALUME_API_KEY, KSOFT_API_KEY, YOUTUBE_API_KEY")
        sys.exit(1)

    if args.merge_strategy == "merge":
        merged = merge_candidates(candidates)
    else:
        merged = pick_best(candidates) or candidates[0]

    force_spanish = (args.lang == "es")
    final_lyrics = postprocess_lyrics(merged, title=title, artist=artist, force_spanish=force_spanish)

    # EXACT OUTPUT
    print(f"{title}//by//{artist}\n")
    print(final_lyrics)


if __name__ == "__main__":
    main()

# end of auto_lyrics_fetcher.py
