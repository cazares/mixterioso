#!/usr/bin/env python3
# scripts/1_txt_mp3.py

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

# ---------------------------------------------------------
# Color constants
# ---------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
WHITE = "\033[97m"

# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "scripts"
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
But with a different query"""


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------
def log(section: str, msg: str, color: str = CYAN) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


# ---------------------------------------------------------
# Slugify
# ---------------------------------------------------------
def slugify(text: str) -> str:
    import re
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


# ---------------------------------------------------------
# Env loader
# ---------------------------------------------------------
def load_env() -> Tuple[str, str]:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", BLUE)
        load_dotenv(env_path)
    else:
        log("ENV", ".env not found, relying on process environment", YELLOW)

    genius_token = os.getenv("GENIUS_ACCESS_TOKEN") or os.getenv("GENIUS_TOKEN")
    mm_api_key = os.getenv("MUSIXMATCH_API_KEY") or os.getenv("MM_API")

    if not genius_token:
        log("ENV", "GENIUS_ACCESS_TOKEN (or GENIUS_TOKEN) is not set.", RED)
    if not mm_api_key:
        log("ENV", "MUSIXMATCH_API_KEY (or MM_API) is not set.", RED)

    if not genius_token or not mm_api_key:
        raise SystemExit(
            f"{RED}Missing required API keys. "
            f"GENIUS_ACCESS_TOKEN and MUSIXMATCH_API_KEY are required.{RESET}"
        )

    return genius_token, mm_api_key


# ---------------------------------------------------------
# Genius search
# ---------------------------------------------------------
def search_genius(query: str, token: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    url = "https://api.genius.com/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"q": query}
    t0 = time.perf_counter()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("response", {}).get("hits", [])
        if not hits:
            log("GENIUS", f'No hits for "{query}"', YELLOW)
            return None, None, None
        result = hits[0]["result"]
        artist = result.get("primary_artist", {}).get("name")
        title = result.get("title")
        gid = result.get("id")
        log("GENIUS", f'Matched: "{artist} - {title}"', GREEN)
        return artist, title, gid
    except Exception as e:
        log("GENIUS", f"Search error: {e}", RED)
        return None, None, None


# ---------------------------------------------------------
# Musixmatch helpers
# ---------------------------------------------------------
def fetch_lyrics_musixmatch(
    query: str,
    artist: Optional[str],
    title: Optional[str],
    api_key: str,
) -> Tuple[Optional[str], Dict[str, Any]]:
    base_params: Dict[str, Any] = {
        "apikey": api_key,
        "f_has_lyrics": 1,
        "s_track_rating": "desc",
        "page_size": 1,
    }

    if artist or title:
        if title:
            base_params["q_track"] = title
        if artist:
            base_params["q_artist"] = artist
        log("MM", f'Searching Musixmatch for "{artist or ""} - {title or ""}"', CYAN)
    else:
        base_params["q"] = query
        log("MM", f'Searching Musixmatch for "{query}"', CYAN)

    try:
        r = requests.get("https://api.musixmatch.com/ws/1.1/track.search",
                         params=base_params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("MM", f"track.search failed: {e}", RED)
        return None, {"musixmatch_error": str(e)}

    body = data.get("message", {}).get("body", {})
    track_list = body.get("track_list", [])
    if not track_list:
        log("MM", "No results.", YELLOW)
        return None, {"musixmatch_status": "no_results"}

    track = track_list[0].get("track", {})
    track_id = track.get("track_id")
    mm_artist = track.get("artist_name")
    mm_title = track.get("track_name")

    log("MM", f'Selected: "{mm_artist} - {mm_title}"', GREEN)

    # Fetch lyrics
    try:
        lr = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.lyrics.get",
            params={"track_id": track_id, "apikey": api_key},
            timeout=10,
        )
        lr.raise_for_status()
        ldata = lr.json()
    except Exception as e:
        log("MM", f"lyrics.get failed: {e}", RED)
        return None, {"musixmatch_status": "lyrics_error", "error": str(e)}

    lyrics = ldata.get("message", {}).get("body", {}).get("lyrics", {}).get("lyrics_body")
    if not lyrics:
        log("MM", "No lyrics body.", YELLOW)
        return None, {"musixmatch_status": "no_lyrics"}

    # Remove Musixmatch footer
    footer = "******* This Lyrics is NOT for Commercial use *******"
    if footer in lyrics:
        lyrics = lyrics.split(footer, 1)[0].strip()

    meta = {
        "artist": mm_artist or artist or "",
        "title": mm_title or title or query,
        "musixmatch_track_id": track_id,
    }
    return lyrics, meta
# ---------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------
def youtube_search_top(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Return top YouTube results using: yt-dlp -j "ytsearchN:<query>".
    """
    log("YT", f'Searching YouTube top {limit}: "{query}"', BLUE)

    try:
        cmd = ["yt-dlp", "-j", f"ytsearch{limit}:{query}"]
        out = subprocess.check_output(cmd, text=True)
    except subprocess.CalledProcessError as e:
        log("YT", f"yt-dlp search failed (exit {e.returncode})", RED)
        return []
    except Exception as e:
        log("YT", f"Error: {e}", RED)
        return []

    results: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if "title" in data and "webpage_url" in data:
                results.append(data)
        except json.JSONDecodeError:
            continue
    return results[:limit]


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    try:
        s = int(seconds)
    except:
        return "?"
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def choose_youtube_result(results: List[Dict[str, Any]], no_ui: bool) -> Optional[Dict[str, Any]]:
    if not results:
        return None

    if no_ui:
        # Pick first result automatically
        selected = results[0]
        log("YT", f'Auto-selected (no-ui): "{selected.get("title")}"', GREEN)
        return selected

    print()
    print(f"{BOLD}{CYAN}Top YouTube results:{RESET}")
    for idx, item in enumerate(results, start=1):
        title = item.get("title") or "(no title)"
        uploader = item.get("uploader") or "unknown"
        dur = fmt_duration(item.get("duration"))
        print(
            f"{WHITE}{idx:2d}.{RESET} "
            f"{GREEN}{title}{RESET} "
            f"{YELLOW}({uploader}, {dur}){RESET}"
        )

    print()
    try:
        choice_raw = input(
            f"{MAGENTA}Pick result # [1–{len(results)}, ENTER=1]: {RESET}"
        ).strip()
    except EOFError:
        choice_raw = ""

    if not choice_raw:
        choice = 1
    else:
        try:
            choice = int(choice_raw)
        except:
            log("YT", f"Invalid selection '{choice_raw}', default=1", YELLOW)
            choice = 1

    if choice < 1 or choice > len(results):
        log("YT", f"Choice {choice} out of range → default=1", YELLOW)
        choice = 1

    selected = results[choice - 1]
    log("YT", f'Selected: "{selected.get("title")}"', GREEN)
    return selected


# ---------------------------------------------------------
# Download MP3 from YouTube
# ---------------------------------------------------------
def youtube_download_mp3_from_url(url: str, slug: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Use yt-dlp to download audio and save as mp3s/<slug>.mp3.
    Return (title, uploader).
    """
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")
    MP3_DIR.mkdir(parents=True, exist_ok=True)

    log("YT", f"Downloading audio→ {slug}.mp3", BLUE)
    print(f"{YELLOW}{url}{RESET}")

    try:
        cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "-o", out_template, url]
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log("YT", f"yt-dlp failed (exit {e.returncode})", RED)
        return None, None

    # Fetch metadata separately
    try:
        meta_json = subprocess.check_output(["yt-dlp", "-j", url], text=True)
        lines = [ln for ln in meta_json.splitlines() if ln.strip()]
        obj = json.loads(lines[-1])
        return obj.get("title"), obj.get("uploader")
    except:
        return None, None


# ---------------------------------------------------------
# Lyrics fallback system
# ---------------------------------------------------------
def fetch_lyrics_with_fallbacks(
    query: str,
    g_artist: Optional[str],
    g_title: Optional[str],
    mm_api_key: str,
    yt_title: Optional[str],
    yt_uploader: Optional[str],
    yt_url: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    """
    Try:
      1) Musixmatch using Genius hints
      2) Musixmatch using YouTube-inferred artist/title
      3) Placeholder
    """

    # Attempt #1 (Genius hints)
    lyrics, meta = fetch_lyrics_musixmatch(query, g_artist, g_title, mm_api_key)
    if lyrics and lyrics.strip():
        meta["lyrics_source"] = "musixmatch_genius"
        meta["youtube_title"] = yt_title
        meta["youtube_uploader"] = yt_uploader
        meta["youtube_url"] = yt_url
        return lyrics, meta

    # Attempt #2 (YouTube-derived)
    yt_meta = {
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }

    candidates: List[Tuple[Optional[str], Optional[str]]] = []
    if yt_title:
        if " - " in yt_title:
            left, right = yt_title.split(" - ", 1)
            left = left.strip()
            right = right.strip()
            candidates.append((left, right))
            candidates.append((right, left))
        else:
            candidates.append((yt_uploader, yt_title))
    elif yt_uploader:
        candidates.append((yt_uploader, query))

    for cand_artist, cand_title in candidates:
        lyrics2, meta2 = fetch_lyrics_musixmatch(query, cand_artist, cand_title, mm_api_key)
        if lyrics2 and lyrics2.strip():
            meta2.update(yt_meta)
            meta2.setdefault("artist", cand_artist or meta2.get("artist") or "")
            meta2.setdefault("title", cand_title or meta2.get("title") or query)
            meta2["lyrics_source"] = "musixmatch_youtube"
            return lyrics2, meta2

    # Fallback placeholder
    final_artist = g_artist or yt_uploader or ""
    final_title = g_title or yt_title or query
    meta = {
        "artist": final_artist,
        "title": final_title,
        "lyrics_source": "placeholder",
        "query": query,
    }
    meta.update(yt_meta)
    return PLACEHOLDER_LYRICS, meta
# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Generate txt+mp3 from query via Genius/Musixmatch/YouTube."
    )
    p.add_argument("query", nargs="*", help="Song search terms")
    p.add_argument("--slug", type=str, help="Override slug")
    p.add_argument("--no-ui", action="store_true", help="Run non-interactively")
    return p.parse_args(argv)


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    no_ui = args.no_ui

    # -----------------------------------------------------
    # Ensure query exists
    # -----------------------------------------------------
    if not args.query:
        if no_ui:
            print(f"{RED}Error: no query provided in --no-ui mode.{RESET}")
            sys.exit(1)

        print()
        print(f"{WHITE}Enter a search query (song/artist):{RESET}")
        raw_query = input(f"{CYAN}> {RESET}").strip()
        if not raw_query:
            print(f"{RED}No query entered. Exiting.{RESET}")
            return
    else:
        raw_query = " ".join(args.query).strip()

    print()
    log("MODE", f'txt+mp3 generation for "{raw_query}"', CYAN)

    # -----------------------------------------------------
    # Load API keys
    # -----------------------------------------------------
    genius_token, mm_api_key = load_env()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------
    # YouTube: Top-10 search
    # -----------------------------------------------------
    results = youtube_search_top(raw_query, limit=10)
    selected = choose_youtube_result(results, no_ui) if results else None

    yt_title = selected.get("title") if selected else None
    yt_uploader = selected.get("uploader") if selected else None
    yt_url = selected.get("webpage_url") if selected else None

    print()
    print(f"{BOLD}{GREEN}YouTube selection summary:{RESET}")
    print(f"  Query:    {WHITE}{raw_query}{RESET}")
    print(f"  Title:    {CYAN}{yt_title or '(none)'}{RESET}")
    print(f"  Uploader: {CYAN}{yt_uploader or '(none)'}{RESET}")
    print(f"  URL:      {BLUE}{yt_url or '(none)'}{RESET}")

    # -----------------------------------------------------
    # Slug selection logic
    # -----------------------------------------------------
    if args.slug:
        slug = slugify(args.slug)
        log("SLUG", f'Using explicit slug "{slug}"', GREEN)
    else:
        base_for_slug = yt_title or raw_query
        suggested = slugify(base_for_slug)

        if no_ui:
            slug = suggested
            log("SLUG", f'Auto-accept slug "{slug}" (--no-ui)', GREEN)
        else:
            print()
            try:
                user_slug = input(
                    f"{MAGENTA}Slug suggestion {RESET}"
                    f"[{GREEN}{suggested}{RESET}] "
                    f"{MAGENTA}(ENTER accepts): {RESET}"
                ).strip()
            except EOFError:
                user_slug = ""

            slug = slugify(user_slug) if user_slug else suggested
            log("SLUG", f'Using slug "{slug}"', GREEN)

    txt_path = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    # -----------------------------------------------------
    # Final confirmation
    # -----------------------------------------------------
    if not no_ui:
        print()
        print(f"{BOLD}{CYAN}Final selection:{RESET}")
        print(f"  Query: {WHITE}{raw_query}{RESET}")
        print(f"  Title: {GREEN}{yt_title or '(unknown)'}{RESET}")
        print(f"  Slug:  {MAGENTA}{slug}{RESET}")
        if yt_url:
            print(f"  URL:   {BLUE}{yt_url}{RESET}")

        try:
            resp = input(
                f"{YELLOW}Proceed? [Y/n]: {RESET}"
            ).strip().lower()
        except EOFError:
            resp = "y"

        if resp not in ("", "y", "yes"):
            log("ABORT", "User cancelled.", RED)
            return

    # -----------------------------------------------------
    # Genius search
    # -----------------------------------------------------
    g_artist, g_title, g_id = search_genius(raw_query, genius_token)

    # -----------------------------------------------------
    # Lyrics + fallback system
    # -----------------------------------------------------
    lyrics_text, lyrics_meta = fetch_lyrics_with_fallbacks(
        raw_query,
        g_artist,
        g_title,
        mm_api_key,
        yt_title,
        yt_uploader,
        yt_url,
    )

    final_artist = lyrics_meta.get("artist") or g_artist or yt_uploader or ""
    final_title = lyrics_meta.get("title") or g_title or yt_title or raw_query

    # -----------------------------------------------------
    # Download MP3
    # -----------------------------------------------------
    if yt_url:
        dl_title, dl_uploader = youtube_download_mp3_from_url(yt_url, slug)
        dl_title = dl_title or yt_title
        dl_uploader = dl_uploader or yt_uploader
    else:
        log("YT", "No URL found; skipping download.", RED)
        dl_title = yt_title
        dl_uploader = yt_uploader

    # -----------------------------------------------------
    # Write TXT Lyrics
    # -----------------------------------------------------
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote lyrics → {txt_path}", GREEN)

    # -----------------------------------------------------
    # Build META JSON
    # -----------------------------------------------------
    meta: Dict[str, Any] = {
        "slug": slug,
        "query": raw_query,
        "artist": final_artist,
        "title": final_title,
        "lyrics_source": lyrics_meta.get("lyrics_source"),
        "musixmatch_track_id": lyrics_meta.get("musixmatch_track_id"),
        "genius_id": g_id,
        "youtube_title": lyrics_meta.get("youtube_title") or dl_title or yt_title,
        "youtube_uploader": lyrics_meta.get("youtube_uploader") or dl_uploader or yt_uploader,
        "youtube_url": lyrics_meta.get("youtube_url") or yt_url,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("META", f"Wrote meta JSON → {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"Audio file at {mp3_path}", GREEN)
    else:
        log("MP3", f"MP3 missing (expected {mp3_path})", YELLOW)

    print()
    print(f"{BOLD}{GREEN}Done.{RESET}")
    print(f"  TXT:  {WHITE}{txt_path}{RESET}")
    print(f"  MP3:  {WHITE}{mp3_path}{RESET}")
    print(f"  META: {WHITE}{meta_path}{RESET}")


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
