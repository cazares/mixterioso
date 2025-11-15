#!/usr/bin/env python3
# scripts/1_txt_mp3.py
# Hybrid Mode:
#   - Interactive UI by default
#   - Headless mode when --no-ui is provided
#   - Intended to work cleanly with 0_master.py in both modes

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# ----- Colors -----
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"

BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"

PLACEHOLDER_LYRICS = """Lyrics not found
We tried Genius,
Musixmatch,
and YouTube
But we still found
0 results for lyrics
Sorry, try again
But with a different query
"""


def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# -------- ENV LOADING (Genius / Musixmatch) --------
def load_env() -> Tuple[str, str]:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    genius_token = os.getenv("GENIUS_ACCESS_TOKEN") or os.getenv("GENIUS_TOKEN")
    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    if not genius_token or not mm_api_key:
        raise SystemExit(
            f"{RED}Missing GENIUS_ACCESS_TOKEN or MUSIXMATCH_API_KEY in env.{RESET}"
        )
    return genius_token, mm_api_key


# -------- GENIUS SEARCH --------
def search_genius(query: str, token: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query}

    try:
        t0 = time.perf_counter()
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get("response", {}).get("hits", [])
        if not hits:
            return None, None, None

        result = hits[0]["result"]
        artist = result.get("primary_artist", {}).get("name")
        title = result.get("title")
        gid = result.get("id")
        t1 = time.perf_counter()
        log("GENIUS", f'Matched: "{artist} - {title}" in {t1 - t0:.2f}s', GREEN)
        return artist, title, gid

    except Exception as e:
        log("GENIUS", f"Genius search failed: {e}", RED)
        return None, None, None


# -------- MUSIXMATCH SEARCH --------
def fetch_lyrics_musixmatch(
    query: str,
    artist: Optional[str],
    title: Optional[str],
    api_key: str,
) -> Tuple[Optional[str], Dict[str, Any]]:
    params = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "s_track_rating": "desc",
        "page_size": 1,
    }

    if artist or title:
        if title:
            params["q_track"] = title
        if artist:
            params["q_artist"] = artist
    else:
        params["q"] = query

    # Search
    try:
        search_url = "https://api.musixmatch.com/ws/1.1/track.search"
        r = requests.get(search_url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return None, {"musixmatch_error": str(e)}

    track_list = data.get("message", {}).get("body", {}).get("track_list", [])
    if not track_list:
        return None, {"musixmatch_status": "no_results"}

    track = track_list[0]["track"]
    track_id = track.get("track_id")
    mm_artist = track.get("artist_name")
    mm_title = track.get("track_name")

    if not track_id:
        return None, {"musixmatch_status": "no_track_id"}

    # Fetch lyrics
    try:
        lyrics_url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
        lr = requests.get(lyrics_url, params={"track_id": track_id, "apikey": api_key}, timeout=10)
        lr.raise_for_status()
        lyrics_data = lr.json()
    except Exception as e:
        return None, {"musixmatch_status": "lyrics_error", "error": str(e)}

    lyrics_obj = lyrics_data.get("message", {}).get("body", {}).get("lyrics", {})
    lyrics_text = lyrics_obj.get("lyrics_body")
    if not lyrics_text:
        return None, {"musixmatch_status": "no_lyrics"}

    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics_text:
        lyrics_text = lyrics_text.split(footer)[0].strip()

    return lyrics_text, {
        "artist": mm_artist,
        "title": mm_title,
        "musixmatch_track_id": track_id,
    }


# -------- YOUTUBE SEARCH --------
def youtube_search_top(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    try:
        cmd = ["yt-dlp", "-j", f"ytsearch{limit}:{query}"]
        out = subprocess.check_output(cmd, text=True)
    except Exception:
        return []
    results = []
    for line in out.splitlines():
        try:
            data = json.loads(line)
            if "title" in data and "webpage_url" in data:
                results.append(data)
        except:
            pass
    return results[:limit]


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def choose_youtube_result(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not results:
        return None

    print(f"\n{BOLD}{CYAN}Top YouTube results:{RESET}")
    for idx, it in enumerate(results, 1):
        title = it.get("title") or "(no title)"
        uploader = it.get("uploader") or "unknown"
        dur = fmt_duration(it.get("duration"))
        print(f"  {BOLD}{idx}. {GREEN}{title}{RESET} {YELLOW}({uploader}, {dur}){RESET}")

    raw = input(f"{BOLD}{MAGENTA}Pick result # [1–{len(results)}, ENTER=1]: {RESET}").strip()
    if not raw:
        choice = 1
    else:
        try:
            choice = int(raw)
            if not (1 <= choice <= len(results)):
                choice = 1
        except:
            choice = 1

    selected = results[choice - 1]
    log("YT", f'Selected: "{selected.get("title")}"', GREEN)
    return selected


def youtube_download_mp3_from_url(url: str, slug: str) -> Tuple[Optional[str], Optional[str]]:
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")

    try:
        cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", out_template, url]
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        return None, None

    try:
        meta_out = subprocess.check_output(["yt-dlp", "-j", url], text=True)
        data = json.loads([ln for ln in meta_out.splitlines() if ln.strip()][-1])
        return data.get("title"), data.get("uploader")
    except:
        return None, None


# -------- LYRICS FALLBACK --------
def fetch_lyrics_with_fallbacks(
    query: str,
    genius_artist: Optional[str],
    genius_title: Optional[str],
    mm_api_key: str,
    yt_title: Optional[str],
    yt_uploader: Optional[str],
    yt_url: Optional[str],
) -> Tuple[str, Dict[str, Any]]:

    # 1) Musixmatch with Genius hints
    lyrics, meta = fetch_lyrics_musixmatch(query, genius_artist, genius_title, mm_api_key)
    if lyrics and lyrics.strip():
        return lyrics, {
            **meta,
            "lyrics_source": "musixmatch_genius",
            "youtube_title": yt_title,
            "youtube_uploader": yt_uploader,
            "youtube_url": yt_url,
        }

    # 2) YouTube-based hints
    candidates: List[Tuple[Optional[str], Optional[str]]] = []
    if yt_title and " - " in yt_title:
        a, b = yt_title.split(" - ", 1)
        candidates.append((a.strip(), b.strip()))
    elif yt_title:
        candidates.append((yt_uploader, yt_title))

    for a, t in candidates:
        lyrics2, meta2 = fetch_lyrics_musixmatch(query, a, t, mm_api_key)
        if lyrics2 and lyrics2.strip():
            return lyrics2, {
                **meta2,
                "lyrics_source": "musixmatch_youtube",
                "youtube_title": yt_title,
                "youtube_uploader": yt_uploader,
                "youtube_url": yt_url,
            }

    # 3) Placeholder
    return PLACEHOLDER_LYRICS, {
        "artist": genius_artist or yt_uploader or "",
        "title": genius_title or yt_title or query,
        "lyrics_source": "placeholder",
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }


# -------- CLI ARGUMENTS --------
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Hybrid txt+mp3 generator")

    # Hybrid mode:
    #   * positional query triggers interactive mode
    #   * flags allow headless mode
    p.add_argument("query", nargs="*", help="Search query words (interactive mode)")
    p.add_argument("--query", dest="flag_query", nargs="+", help="Search query (headless)")
    p.add_argument("--slug", help="Slug (headless only)")
    p.add_argument("--no-ui", action="store_true", help="Run in headless mode")
    return p.parse_args(argv)


# -------- MAIN --------
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    # Determine mode
    headless = args.no_ui

    # HEADLESS MODE REQUIRES BOTH --slug AND --query
    if headless:
        if not args.slug or not args.flag_query:
            raise SystemExit(
                f"{RED}--no-ui requires --slug and --query flags.{RESET}"
            )

        slug = slugify(args.slug)
        raw_query = " ".join(args.flag_query).strip()

        TXT_DIR.mkdir(parents=True, exist_ok=True)
        MP3_DIR.mkdir(parents=True, exist_ok=True)
        META_DIR.mkdir(parents=True, exist_ok=True)

        # Search YouTube non-interactively
        results = youtube_search_top(raw_query, limit=5)
        selected = results[0] if results else None

        yt_title = selected.get("title") if selected else None
        yt_uploader = selected.get("uploader") if selected else None
        yt_url = selected.get("webpage_url") if selected else None

        # Lyrics: still use Genius/Musixmatch
        genius_token, mm_api_key = load_env()
        g_artist, g_title, g_id = search_genius(raw_query, genius_token)

        lyrics_text, lyrics_meta = fetch_lyrics_with_fallbacks(
            raw_query,
            g_artist,
            g_title,
            mm_api_key,
            yt_title,
            yt_uploader,
            yt_url,
        )

        # Download MP3
        if yt_url:
            dl_title, dl_uploader = youtube_download_mp3_from_url(yt_url, slug)

        # Write TXT
        txt_path = TXT_DIR / f"{slug}.txt"
        txt_path.write_text(lyrics_text, encoding="utf-8")

        # Write META
        meta = {
            "slug": slug,
            "query": raw_query,
            "artist": lyrics_meta.get("artist") or "",
            "title": lyrics_meta.get("title") or raw_query,
            "lyrics_source": lyrics_meta.get("lyrics_source"),
            "musixmatch_track_id": lyrics_meta.get("musixmatch_track_id"),
            "genius_id": g_id,
            "youtube_title": yt_title,
            "youtube_uploader": yt_uploader,
            "youtube_url": yt_url,
        }
        (META_DIR / f"{slug}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return

    # --------------------
    # INTERACTIVE MODE
    # --------------------
    if not args.query:
        raise SystemExit(f"{RED}Interactive mode requires a positional query.{RESET}")

    raw_query = " ".join(args.query).strip()
    genius_token, mm_api_key = load_env()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # ---- YouTube top-10 + user selection ----
    results = youtube_search_top(raw_query, limit=10)
    selected = choose_youtube_result(results) if results else None

    yt_title = selected.get("title") if selected else None
    yt_uploader = selected.get("uploader") if selected else None
    yt_url = selected.get("webpage_url") if selected else None

    # Summary
    print(f"\n{BOLD}{GREEN}YouTube selection summary:{RESET}")
    print(f"  Query:    {YELLOW}{raw_query}{RESET}")
    print(f"  Title:    {CYAN}{yt_title}{RESET}")
    print(f"  Uploader: {CYAN}{yt_uploader}{RESET}")
    print(f"  URL:      {BLUE}{yt_url}{RESET}")

    # ---- Slug suggestion ----
    suggested_slug = slugify(yt_title or raw_query)
    user_slug = input(
        f"{BOLD}{MAGENTA}Suggested slug{RESET} "
        f"[{GREEN}{suggested_slug}{RESET}] "
        f"(ENTER to accept): "
    ).strip()

    slug = slugify(user_slug) if user_slug else suggested_slug

    txt_path = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    # ---- Confirm ----
    print(f"\n{BOLD}{CYAN}Final selection:{RESET}")
    print(f"  Slug:  {MAGENTA}{slug}{RESET}")
    ans = input(f"{BOLD}{YELLOW}Proceed? [Y/n]: {RESET}").strip().lower()
    if ans not in ("", "y", "yes"):
        log("ABORT", "User cancelled.", RED)
        return

    # ---- Genius ----
    g_artist, g_title, g_id = search_genius(raw_query, genius_token)

    # ---- Lyrics ----
    lyrics_text, lyrics_meta = fetch_lyrics_with_fallbacks(
        raw_query,
        g_artist,
        g_title,
        mm_api_key,
        yt_title,
        yt_uploader,
        yt_url,
    )

    # ---- Download mp3 ----
    if yt_url:
        dl_title, dl_uploader = youtube_download_mp3_from_url(yt_url, slug)

    # ---- Write TXT ----
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Saved lyrics to {txt_path}", GREEN)

    # ---- Write META ----
    meta = {
        "slug": slug,
        "query": raw_query,
        "artist": lyrics_meta.get("artist"),
        "title": lyrics_meta.get("title"),
        "lyrics_source": lyrics_meta.get("lyrics_source"),
        "musixmatch_track_id": lyrics_meta.get("musixmatch_track_id"),
        "genius_id": g_id,
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log("META", f"Wrote metadata to {meta_path}", GREEN)

    print(f"\n{BOLD}{GREEN}Done.{RESET}")
    print(f"TXT:  {txt_path}")
    print(f"MP3:  {mp3_path}")
    print(f"META: {meta_path}")


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
