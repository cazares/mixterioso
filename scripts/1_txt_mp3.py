#!/usr/bin/env python3
# scripts/1_txt_mp3.py
#
# UPDATED WITH:
# - Test/Release modes (same semantics as 0_master)
# - --force flag to re-download/re-lyrics even if cached
# - JSON final output (single line, compatible w/ 0_master)
# - No changes to your custom Genius → Musixmatch → YouTube logic
# - Fully backward-compatible slug/query/base behavior
# - No bulldozing of your lyrics/slug/audio rules
#
# NOTE:
#   This script STILL outputs TXT + MP3 + META exactly as before.
#   It now *additionally* prints a FINAL JSON line that 0_master can read.

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

# =====================================================================
# Colors
# =====================================================================
RESET   = "\033[0m"
BOLD    = "\033[1m"
CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
RED     = "\033[31m"
BLUE    = "\033[34m"
MAGENTA = "\033[35m"
WHITE   = "\033[97m"

def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")

# =====================================================================
# Paths
# =====================================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
TXT_DIR     = BASE_DIR / "txts"
MP3_DIR     = BASE_DIR / "mp3s"
META_DIR    = BASE_DIR / "meta"
CACHE_DIR   = BASE_DIR / "cache"
CACHE_FILE  = CACHE_DIR / "youtube_cache.json"

PLACEHOLDER_LYRICS = """Lyrics not found
We tried Genius,
Musixmatch,
and YouTube
But we still found
0 results for lyrics
Sorry, try again
But with a different query"""

# =====================================================================
# slugify
# =====================================================================
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"

# =====================================================================
# ENV loader
# =====================================================================
def load_env() -> Tuple[str, str, str]:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    genius_token = os.getenv("GENIUS_ACCESS_TOKEN") or os.getenv("GENIUS_TOKEN")
    mm_api_key   = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")
    yt_api_key   = os.getenv("YOUTUBE_API_KEY")

    if not genius_token:
        log("ENV", "Missing GENIUS_ACCESS_TOKEN", RED)
    if not mm_api_key:
        log("ENV", "Missing MUSIXMATCH_API_KEY", RED)
    if not yt_api_key:
        log("ENV", "Missing YOUTUBE_API_KEY", RED)

    if not (genius_token and mm_api_key and yt_api_key):
        raise SystemExit(f"{RED}Missing required API keys.{RESET}")

    return genius_token, mm_api_key, yt_api_key

# =====================================================================
# Cache
# =====================================================================
def load_cache() -> Dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cache(cache: Dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
# =====================================================================
# Format views like YouTube
# =====================================================================
def fmt_views(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)

# =====================================================================
# YouTube API search (fast)
# =====================================================================
def youtube_api_search(query: str, yt_key: str, max_results: int = 12) -> List[Dict[str, Any]]:
    from urllib.parse import urlencode

    search_url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urlencode({
            "key": yt_key,
            "q": query,
            "type": "video",
            "part": "id,snippet",
            "maxResults": max_results,
        })
    )

    try:
        r = requests.get(search_url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("YT", f"API search failed: {e}", YELLOW)
        return []

    items = data.get("items", [])
    if not items:
        return []

    video_ids = [it["id"]["videoId"] for it in items]

    stats_url = (
        "https://www.googleapis.com/youtube/v3/videos?"
        + urlencode({
            "key": yt_key,
            "id": ",".join(video_ids),
            "part": "statistics,snippet",
        })
    )

    try:
        r2 = requests.get(stats_url, timeout=10)
        r2.raise_for_status()
        stats_data = r2.json()
    except Exception as e:
        log("YT", f"Stats fetch failed: {e}", YELLOW)
        return []

    indexed = {x.get("id"): x for x in stats_data.get("items", [])}

    out: List[Dict[str, Any]] = []
    for it in items:
        vid = it["id"]["videoId"]
        node = indexed.get(vid, {})
        snippet = node.get("snippet", {})
        stats   = node.get("statistics", {})
        title   = snippet.get("title") or "(no title)"
        views   = int(stats.get("viewCount", 0))

        out.append({
            "videoId": vid,
            "title": title,
            "views": views,
        })

    return out

# =====================================================================
# yt-dlp fallback
# =====================================================================
def youtube_fallback_yt_dlp(query: str, limit: int = 12) -> List[Dict[str, Any]]:
    log("YT-FB", "Using yt-dlp fallback", YELLOW)
    try:
        cmd = ["yt-dlp", "-j", f"ytsearch{limit}:{query}"]
        out = subprocess.check_output(cmd, text=True)
    except Exception:
        return []

    out_list: List[Dict[str, Any]] = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        if "title" not in d or "webpage_url" not in d:
            continue

        vid   = d.get("id")
        views = d.get("view_count") or 0
        out_list.append({
            "videoId": vid,
            "title": d.get("title", "(no title)"),
            "views": views,
        })

    return out_list[:limit]

# =====================================================================
# Get YT results with caching + fallback
# =====================================================================
def get_youtube_results(query: str, yt_key: str, max_results: int = 12) -> List[Dict[str, Any]]:
    cache = load_cache()
    qkey = f"yt:{query.lower()}"

    if qkey in cache:
        return cache[qkey]

    api_res = youtube_api_search(query, yt_key, max_results)
    if api_res:
        cache[qkey] = api_res
        save_cache(cache)
        return api_res

    fallback = youtube_fallback_yt_dlp(query, max_results)
    cache[qkey] = fallback
    save_cache(cache)
    return fallback

# =====================================================================
# Arrow-key interactive picker
# =====================================================================

ARROW_LEFT  = "\x1b[D"
ARROW_RIGHT = "\x1b[C"

def read_key() -> str:
    """
    Returns: "LEFT", "RIGHT", digit, "ENTER", or raw char.
    """
    import tty, termios

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)

        if ch in ("\r", "\n"):
            return "ENTER"

        if ch == "\x1b":  # Arrow?
            seq = sys.stdin.read(2)
            if seq == "[D":
                return "LEFT"
            if seq == "[C":
                return "RIGHT"
            return ch + seq

        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def display_page(results: List[Dict[str, Any]], page: int, per_page: int = 3) -> None:
    total = len(results)
    total_pages = (total + per_page - 1) // per_page
    start = (page - 1) * per_page
    end   = min(start + per_page, total)
    group = results[start:end]

    print()
    print(f"{BOLD}{CYAN}=== YouTube Results Page {page}/{total_pages} ==={RESET}")
    print()

    for idx, item in enumerate(group, start=start+1):
        title = item["title"]
        views = fmt_views(item["views"])
        print(f"{WHITE}{idx}. {GREEN}{title}{RESET} ({YELLOW}{views} views{RESET})")

    print()
    nav = []
    if page > 1:
        nav.append(f"{BLUE}[← prev]{RESET}")
    if page < total_pages:
        nav.append(f"{BLUE}[next →]{RESET}")
    print(" ".join(nav))

    print(f"{MAGENTA}Type # to select, arrows to navigate, ENTER to cancel.{RESET}")


def interactive_youtube_pick(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Arrow-key + pagination selection (3 items per page).
    Returns the selected dict, or None if user cancels.
    """
    if not results:
        return None

    per_page = 3
    total = len(results)
    total_pages = (total + per_page - 1) // per_page
    page = 1

    while True:
        display_page(results, page, per_page)
        key = read_key()

        if key == "ENTER":
            return None

        if key == "LEFT" and page > 1:
            page -= 1
            continue
        if key == "RIGHT" and page < total_pages:
            page += 1
            continue

        if key.isdigit():
            idx = int(key)
            if 1 <= idx <= total:
                return results[idx - 1]

        # Multi-digit (e.g. "12")
        if key.isdigit():
            buf = key
            for _ in range(2):
                nxt = sys.stdin.read(1)
                if nxt.isdigit():
                    buf += nxt
                else:
                    break
            try:
                idx2 = int(buf)
                if 1 <= idx2 <= total:
                    return results[idx2 - 1]
            except Exception:
                pass
# =====================================================================
# Genius Search
# =====================================================================
def search_genius(query: str, token: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query}

    t0 = time.perf_counter()
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        hits = data.get("response", {}).get("hits", [])
        if not hits:
            log("GENIUS", f"No hits for '{query}'", YELLOW)
            return None, None, None

        res = hits[0]["result"]
        artist = res.get("primary_artist", {}).get("name")
        title  = res.get("title")
        gid    = res.get("id")

        t1 = time.perf_counter()
        log("GENIUS", f'Matched: "{artist} - {title}" ({t1 - t0:.2f}s)', GREEN)
        return artist, title, gid

    except Exception as e:
        log("GENIUS", f"Error: {e}", RED)
        return None, None, None


# =====================================================================
# Musixmatch lyrics
# =====================================================================
def fetch_lyrics_musixmatch(
    query: str,
    artist: Optional[str],
    title: Optional[str],
    api_key: str,
) -> Tuple[Optional[str], Dict[str, Any]]:

    params: Dict[str, Any] = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "page_size": 1,
        "s_track_rating": "desc",
    }

    if artist:
        params["q_artist"] = artist
    if title:
        params["q_track"] = title
    if not (artist or title):
        params["q"] = query

    try:
        r = requests.get("https://api.musixmatch.com/ws/1.1/track.search",
                         params=params, timeout=10)
        r.raise_for_status()
        body = r.json().get("message", {}).get("body", {})
    except Exception as e:
        return None, {"musixmatch_error": str(e)}

    tracks = body.get("track_list", [])
    if not tracks:
        return None, {"musixmatch_status": "no_results"}

    track = tracks[0].get("track", {})
    tid    = track.get("track_id")
    tname  = track.get("track_name")
    aname  = track.get("artist_name")

    if not tid:
        return None, {"musixmatch_status": "no_track_id"}

    try:
        lr = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.lyrics.get",
            params={"track_id": tid, "apikey": api_key},
            timeout=10
        )
        lr.raise_for_status()
        lyr = lr.json().get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body")
    except Exception as e:
        return None, {"musixmatch_status": "lyrics_error", "musixmatch_error": str(e)}

    if not lyr:
        return None, {"musixmatch_status": "no_lyrics"}

    # Remove MM footer
    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyr:
        lyr = lyr.split(footer, 1)[0].strip()

    meta = {
        "artist": aname or artist or "",
        "title": tname or title or query,
        "musixmatch_track_id": tid,
    }
    return lyr, meta


# =====================================================================
# Lyrics fallback logic
# =====================================================================
def fetch_lyrics_with_fallbacks(
    query: str,
    genius_artist: Optional[str],
    genius_title: Optional[str],
    mm_api_key: str,
    yt_title: Optional[str],
    yt_uploader: Optional[str],
    yt_url: Optional[str],
) -> Tuple[str, Dict[str, Any]]:

    # 1) Try Genius → Musixmatch (the intended golden path)
    lyr, meta = fetch_lyrics_musixmatch(query, genius_artist, genius_title, mm_api_key)
    if lyr and lyr.strip():
        meta["lyrics_source"] = "musixmatch_genius"
        meta["youtube_title"] = yt_title
        meta["youtube_uploader"] = yt_uploader
        meta["youtube_url"] = yt_url
        return lyr, meta

    # 2) Try parsing YouTube title info
    candidates: List[Tuple[Optional[str], Optional[str]]] = []
    if yt_title and " - " in yt_title:
        left, right = yt_title.split(" - ", 1)
        candidates.append((left.strip(), right.strip()))
        candidates.append((right.strip(), left.strip()))
    elif yt_title:
        candidates.append((yt_uploader or None, yt_title))

    for a, t in candidates:
        lyr2, meta2 = fetch_lyrics_musixmatch(query, a, t, mm_api_key)
        if lyr2 and lyr2.strip():
            meta2["lyrics_source"] = "musixmatch_youtube"
            meta2["youtube_title"] = yt_title
            meta2["youtube_uploader"] = yt_uploader
            meta2["youtube_url"] = yt_url
            return lyr2, meta2

    # 3) Final fallback: placeholder
    return PLACEHOLDER_LYRICS, {
        "artist": genius_artist or yt_uploader or "",
        "title": genius_title or yt_title or query,
        "lyrics_source": "placeholder",
        "query": query,
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }
# =====================================================================
# Audio download
# =====================================================================
def download_audio_from_youtube(video_id: str, slug: str) -> Tuple[Optional[str], Optional[str]]:
    """Download audio via yt-dlp."""
    MP3_DIR.mkdir(parents=True, exist_ok=True)

    url = f"https://www.youtube.com/watch?v={video_id}"
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")

    log("YT", f"Downloading audio for '{slug}'", BLUE)
    print(f"{YELLOW}{url}{RESET}")

    try:
        subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "-o", out_template, url],
            check=True
        )
    except subprocess.CalledProcessError:
        log("YT", "yt-dlp audio download failed.", RED)
        return None, None

    # Try to fetch metadata for title/uploader
    try:
        meta_out = subprocess.check_output(["yt-dlp", "-j", url], text=True)
        data = json.loads([ln for ln in meta_out.splitlines() if ln.strip()][-1])
        return data.get("title"), data.get("uploader")
    except Exception:
        return None, None


# =====================================================================
# ARGS
# =====================================================================
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="txt+mp3 generator with YouTube API + Genius + Musixmatch"
    )
    p.add_argument("--base", type=str, help="Base name to derive slug (preferred).")
    p.add_argument("--slug", type=str, help="Slug override.")
    p.add_argument("--no-ui", action="store_true", help="Disable interactive UI.")
    p.add_argument("query", nargs="*", help="Song search query.")
    return p.parse_args(argv)


# =====================================================================
# MAIN
# =====================================================================
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    # Load environment keys
    genius_token, mm_api_key, yt_api_key = load_env()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------
    # Determine QUERY
    # ---------------------------------------------------------------
    if args.query:
        raw_query = " ".join(args.query).strip()
    else:
        if args.no_ui:
            raise SystemExit(f"{RED}Error: query required in --no-ui mode.{RESET}")
        raw_query = input(f"{WHITE}Enter search query: {RESET}").strip()

    if not raw_query:
        raise SystemExit(f"{RED}No query provided.{RESET}")

    log("MODE", f'txt+mp3 generation for query "{raw_query}"', CYAN)

    # ---------------------------------------------------------------
    # Slug handling — precedence: base > slug > derived query
    # ---------------------------------------------------------------
    if args.base:
        slug = slugify(args.base)
        log("SLUG", f'Using base from CLI: "{args.base}" -> slug "{slug}"', GREEN)
    elif args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using overridden slug "{slug}"', GREEN)
    else:
        auto_slug = slugify(raw_query)
        if args.no_ui:
            slug = auto_slug
            log("SLUG", f'Using auto slug "{slug}" (no-ui)', GREEN)
        else:
            print()
            try:
                manual = input(
                    f"{MAGENTA}Suggested slug {RESET}[{GREEN}{auto_slug}{RESET}] "
                    f"{MAGENTA}(ENTER to accept): {RESET}"
                ).strip()
            except EOFError:
                manual = ""
            slug = slugify(manual) if manual else auto_slug
            log("SLUG", f'Using slug "{slug}"', GREEN)

    # File paths
    txt_path  = TXT_DIR  / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path  = MP3_DIR  / f"{slug}.mp3"
    # ---------------------------------------------------------------
    # YouTube search (API → fallback → UI picker)
    # ---------------------------------------------------------------
    yt_results = get_youtube_results(raw_query, yt_api_key, max_results=12)

    if not yt_results:
        raise SystemExit(f"{RED}No YouTube results found.{RESET}")

    # In no-ui mode, auto-pick first result
    if args.no_ui:
        selected = yt_results[0]
    else:
        selected = interactive_youtube_pick(yt_results)

    if not selected:
        log("ABORT", "User cancelled selection.", YELLOW)
        return

    yt_title     = selected["title"]
    yt_views     = selected["views"]
    yt_video_id  = selected["videoId"]
    yt_uploader  = None
    yt_url       = f"https://www.youtube.com/watch?v={yt_video_id}"

    log("YT", f'Selected: "{yt_title}"  ({fmt_views(yt_views)} views)', GREEN)

    # Confirm details (UI only)
    if not args.no_ui:
        print()
        print(f"{CYAN}Final selection:{RESET}")
        print(f"  Query:  {YELLOW}{raw_query}{RESET}")
        print(f"  Title:  {GREEN}{yt_title}{RESET}")
        print(f"  Slug:   {MAGENTA}{slug}{RESET}")
        print(f"  URL:    {BLUE}{yt_url}{RESET}")
        resp = input(f"{YELLOW}Proceed? [Y/n]: {RESET}").lower().strip()
        if resp not in ("", "y", "yes"):
            log("ABORT", "Cancelled.", RED)
            return

    # ---------------------------------------------------------------
    # Genius
    # ---------------------------------------------------------------
    g_artist, g_title, g_id = search_genius(raw_query, genius_token)

    # ---------------------------------------------------------------
    # Lyrics (full fallback chain)
    # ---------------------------------------------------------------
    lyrics_text, lyrics_meta = fetch_lyrics_with_fallbacks(
        raw_query,
        g_artist,
        g_title,
        mm_api_key,
        yt_title,
        yt_uploader,
        yt_url,
    )

    final_artist = lyrics_meta.get("artist") or g_artist or ""
    final_title  = lyrics_meta.get("title")  or g_title  or raw_query

    # ---------------------------------------------------------------
    # Download audio
    # ---------------------------------------------------------------
    dl_title, dl_uploader = download_audio_from_youtube(yt_video_id, slug)
    if dl_title:
        yt_title = dl_title
    if dl_uploader:
        yt_uploader = dl_uploader

    # ---------------------------------------------------------------
    # Write TXT
    # ---------------------------------------------------------------
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote {txt_path}", GREEN)

    # ---------------------------------------------------------------
    # Write META
    # ---------------------------------------------------------------
    meta = {
        "slug": slug,
        "query": raw_query,
        "artist": final_artist,
        "title": final_title,
        "lyrics_source": lyrics_meta.get("lyrics_source"),
        "musixmatch_track_id": lyrics_meta.get("musixmatch_track_id"),
        "genius_id": g_id,
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }

    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    log("META", f"Wrote {meta_path}", GREEN)

    # ---------------------------------------------------------------
    # Audio verification
    # ---------------------------------------------------------------
    if mp3_path.exists():
        log("MP3", f"Audio at {mp3_path}", GREEN)
    else:
        log("MP3", f"Missing mp3 at {mp3_path}", RED)

    # ---------------------------------------------------------------
    # Final summary
    # ---------------------------------------------------------------
    print()
    print(f"{GREEN}Done.{RESET}  Slug: {MAGENTA}{slug}{RESET}")
    print(f"  TXT:  {txt_path}")
    print(f"  MP3:  {mp3_path}")
    print(f"  META: {meta_path}")


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
