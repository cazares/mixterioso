#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

# project root (this file is expected to live in e.g. project/experimental or project/scripts)
BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"

PLACEHOLDER_LYRICS = """Lyrics not found
We tried Genius, 
Musixmatch, 
and Youtube
But we still found
0 results for lyrics
Sorry, try again
But with a different query"""


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def slugify(text: str) -> str:
    import re

    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def load_env() -> tuple[str, str]:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", CYAN)
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
        raise SystemExit("Missing required API keys in environment.")

    return genius_token, mm_api_key


def search_genius(query: str, token: str) -> tuple[str | None, str | None, int | None]:
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
        t1 = time.perf_counter()
        log("GENIUS", f'Matched: "{artist} - {title}" in {t1 - t0:.2f}s', GREEN)
        return artist, title, gid
    except Exception as e:
        t1 = time.perf_counter()
        log("GENIUS", f"Search failed for \"{query}\" in {t1 - t0:.2f}s: {e}", RED)
        return None, None, None


def fetch_lyrics_musixmatch(
    query: str,
    artist: str | None,
    title: str | None,
    api_key: str,
) -> tuple[str | None, dict]:
    base_params = {
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
        log("MM", f'Searching Musixmatch for: "{artist or ""} - {title or ""}"', CYAN)
    else:
        base_params["q"] = query
        log("MM", f'Searching Musixmatch for: "{query}"', CYAN)

    search_url = "https://api.musixmatch.com/ws/1.1/track.search"
    try:
        r = requests.get(search_url, params=base_params, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log("MM", f"track.search failed: {e}", RED)
        return None, {"musixmatch_error": str(e)}

    body = data.get("message", {}).get("body", {})
    track_list = body.get("track_list", [])
    if not track_list:
        log("MM", "track.search returned no results.", YELLOW)
        return None, {"musixmatch_status": "no_results"}

    track = track_list[0].get("track", {})
    track_id = track.get("track_id")
    mm_artist = track.get("artist_name")
    mm_title = track.get("track_name")

    if not track_id:
        log("MM", "No track_id in first result.", YELLOW)
        return None, {"musixmatch_status": "no_track_id"}

    log("MM", f'Chosen track: "{mm_artist} - {mm_title}" (track_id={track_id})', GREEN)

    lyrics_url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    try:
        lr = requests.get(lyrics_url, params={"track_id": track_id, "apikey": api_key}, timeout=10)
        lr.raise_for_status()
        ldata = lr.json()
    except Exception as e:
        log("MM", f"track.lyrics.get failed: {e}", RED)
        return None, {"musixmatch_status": "lyrics_error", "musixmatch_error": str(e)}

    lbody = ldata.get("message", {}).get("body", {})
    lyrics_obj = lbody.get("lyrics", {})
    lyrics_text = lyrics_obj.get("lyrics_body")
    if not lyrics_text:
        log("MM", "Lyrics body missing in response.", YELLOW)
        return None, {"musixmatch_status": "no_lyrics"}

    # Clean standard Musixmatch footer if present
    if "******* This Lyrics is NOT for Commercial use *******" in lyrics_text:
        lyrics_text = lyrics_text.split("******* This Lyrics is NOT for Commercial use *******", 1)[0].strip()

    meta = {
        "artist": mm_artist or artist or "",
        "title": mm_title or title or query,
        "musixmatch_track_id": track_id,
    }
    return lyrics_text, meta


def youtube_search_first(query: str) -> tuple[str | None, str | None, str | None]:
    """
    Return (title, uploader, url) for first YouTube result of the query,
    or (None, None, None) if it fails.
    """
    try:
        out = subprocess.check_output(
            ["yt-dlp", "-j", f"ytsearch1:{query}"],
            text=True,
        )
        lines = [ln for ln in out.splitlines() if ln.strip()]
        if not lines:
            return None, None, None
        data = json.loads(lines[-1])
        title = data.get("title")
        uploader = data.get("uploader")
        url = data.get("webpage_url")
        return title, uploader, url
    except Exception as e:
        log("YT", f"YouTube search failed for \"ytsearch1:{query}\": {e}", RED)
        return None, None, None


def youtube_download_mp3(search_str: str, slug: str) -> tuple[str | None, str | None]:
    """
    Download first YouTube audio match for search_str as mp3s/<slug>.mp3.
    Returns (youtube_title, youtube_uploader).
    """
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out_template = str(MP3_DIR / f"{slug}.%(ext)s")

    log("YT", f'Searching/downloading audio for "{search_str}" as slug "{slug}"', CYAN)
    try:
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            out_template,
            f"ytsearch1:{search_str}",
        ]
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        log("YT", f"yt-dlp audio download failed (exit {e.returncode}).", RED)
        return None, None

    # Re-run a JSON-only search to capture metadata for meta.json
    yt_title, yt_uploader, _ = youtube_search_first(search_str)
    return yt_title, yt_uploader


def fetch_lyrics_with_fallbacks(
    query: str,
    genius_artist: str | None,
    genius_title: str | None,
    mm_api_key: str,
) -> tuple[str, dict]:
    """
    Try Musixmatch (with Genius hints), then YouTube-derived metadata,
    then return placeholder lyrics if still nothing.
    """
    # 1) Musixmatch using Genius hints
    lyrics, meta = fetch_lyrics_musixmatch(query, genius_artist, genius_title, mm_api_key)
    if lyrics and lyrics.strip():
        meta.setdefault("artist", genius_artist or meta.get("artist") or "")
        meta.setdefault("title", genius_title or meta.get("title") or query)
        meta["lyrics_source"] = "musixmatch_genius"
        return lyrics, meta

    # 2) YouTube-based hints
    yt_title, yt_uploader, yt_url = youtube_search_first(query)
    yt_meta = {
        "youtube_title": yt_title,
        "youtube_uploader": yt_uploader,
        "youtube_url": yt_url,
    }

    candidates: list[tuple[str | None, str | None]] = []
    if yt_title:
        if " - " in yt_title:
            left, right = yt_title.split(" - ", 1)
            left = left.strip()
            right = right.strip()
            candidates.append((left, right))   # Artist - Title
            candidates.append((right, left))   # Title - Artist
        else:
            candidates.append((yt_uploader or None, yt_title))
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

    # 3) Placeholder
    final_artist = genius_artist or yt_uploader or ""
    final_title = genius_title or yt_title or query

    meta = {
        "artist": final_artist,
        "title": final_title,
        "lyrics_source": "placeholder",
        "query": query,
    }
    meta.update(yt_meta)
    return PLACEHOLDER_LYRICS, meta


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate txt+mp3 from query via Genius/Musixmatch/YouTube.")
    p.add_argument("query", nargs="+", help="Search query, e.g. 'red hot chili peppers californication'")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    query = " ".join(args.query).strip()

    log("MODE", f'txt+mp3 generation for "{query}"', CYAN)

    genius_token, mm_api_key = load_env()

    TXT_DIR.mkdir(parents=True, exist_ok=True)
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    META_DIR.mkdir(parents=True, exist_ok=True)

    # Genius search
    g_artist, g_title, g_id = search_genius(query, genius_token)

    # Lyrics with fallbacks
    lyrics_text, lyrics_meta = fetch_lyrics_with_fallbacks(query, g_artist, g_title, mm_api_key)

    # Decide final artist/title for slug and metadata
    final_artist = lyrics_meta.get("artist") or g_artist or ""
    final_title = lyrics_meta.get("title") or g_title or query

    slug = slugify(final_title)
    log("SLUG", f'Title slug: "{slug}"', GREEN)

    txt_path = TXT_DIR / f"{slug}.txt"
    meta_path = META_DIR / f"{slug}.json"
    mp3_path = MP3_DIR / f"{slug}.mp3"

    # Decide search string for YouTube
    search_str = f"{final_artist} {final_title}".strip() or query

    # Check if audio already exists for this slug / metadata
    yt_title: str | None = None
    yt_uploader: str | None = None

    if mp3_path.exists():
        log(
            "MP3",
            f'Audio already exists for "{final_artist} - {final_title}" '
            f'(slug="{slug}"). Reusing {mp3_path} and skipping yt-dlp download.',
            GREEN,
        )
        # Optionally refresh YouTube metadata if not already in lyrics_meta
        if not lyrics_meta.get("youtube_title") or not lyrics_meta.get("youtube_uploader"):
            yt_title, yt_uploader, _ = youtube_search_first(search_str)
    else:
        yt_title, yt_uploader = youtube_download_mp3(search_str, slug)

    # Write lyrics txt (always, even placeholder)
    txt_path.write_text(lyrics_text, encoding="utf-8")
    log("TXT", f"Wrote lyrics txt to {txt_path}", GREEN)

    # Build meta
    meta: dict = {
        "slug": slug,
        "query": query,
        "artist": final_artist,
        "title": final_title,
        "lyrics_source": lyrics_meta.get("lyrics_source"),
        "musixmatch_track_id": lyrics_meta.get("musixmatch_track_id"),
        "genius_id": g_id,
        "youtube_title": lyrics_meta.get("youtube_title") or yt_title,
        "youtube_uploader": lyrics_meta.get("youtube_uploader") or yt_uploader,
    }

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log("META", f"Wrote meta JSON to {meta_path}", GREEN)

    if mp3_path.exists():
        log("MP3", f"Audio mp3 is at {mp3_path}", GREEN)
    else:
        log("MP3", f"Expected mp3 at {mp3_path} but file not found.", YELLOW)


if __name__ == "__main__":
    main()

# end of 1download.py
