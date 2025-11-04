#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from dotenv import load_dotenv

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


BASE_DIR = Path(__file__).resolve().parent.parent
TXT_DIR = BASE_DIR / "txts"
MP3_DIR = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"


def slugify(text: str) -> str:
    base = text.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def load_env() -> None:
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        log("ENV", f"Loading .env from {env_path}", GREEN)
        load_dotenv(env_path)
    else:
        log("ENV", f"No .env found at {env_path}, relying on process env", YELLOW)


def get_env_or_die(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise SystemExit(f"Error: {name} is not set.")
    return val


def genius_search(query: str, token: str) -> dict:
    url = "https://api.genius.com/search"
    params = {"q": query}
    headers = {"Authorization": f"Bearer {token}"}
    t0 = time.perf_counter()
    r = requests.get(url, params=params, headers=headers, timeout=10)
    t1 = time.perf_counter()
    if r.status_code != 200:
        raise SystemExit(f"Genius search failed: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    hits = data.get("response", {}).get("hits", [])
    if not hits:
        raise SystemExit("No Genius hits found for query.")
    hit = hits[0]["result"]
    primary_artist = hit["primary_artist"]["name"]
    title = hit["title"]
    full_title = hit.get("full_title", f"{primary_artist} - {title}")
    log("GENIUS", f"Matched: \"{primary_artist} - {title}\" in {t1 - t0:.2f}s", GREEN)
    return {
        "artist": primary_artist,
        "title": title,
        "full_title": full_title,
    }


def musixmatch_search_track(artist: str, title: str, api_key: str) -> int:
    url = "https://api.musixmatch.com/ws/1.1/track.search"
    params = {
        "q_track": title,
        "q_artist": artist,
        "f_has_lyrics": 1,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": api_key,
    }
    t0 = time.perf_counter()
    r = requests.get(url, params=params, timeout=10)
    t1 = time.perf_counter()
    if r.status_code != 200:
        raise SystemExit(f"Musixmatch track.search failed: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    message = data.get("message", {})
    body = message.get("body", {})
    track_list = body.get("track_list", [])
    if not track_list:
        raise SystemExit("Musixmatch track.search returned no results.")
    track = track_list[0]["track"]
    track_id = track["track_id"]
    log(
        "MM",
        f"Chosen track: \"{track['artist_name']} - {track['track_name']}\" "
        f"(track_id={track_id}) in {t1 - t0:.2f}s",
        GREEN,
    )
    return track_id


def musixmatch_get_lyrics(track_id: int, api_key: str) -> str:
    url = "https://api.musixmatch.com/ws/1.1/track.lyrics.get"
    params = {"track_id": track_id, "apikey": api_key}
    t0 = time.perf_counter()
    r = requests.get(url, params=params, timeout=10)
    t1 = time.perf_counter()
    if r.status_code != 200:
        raise SystemExit(f"Musixmatch track.lyrics.get failed: HTTP {r.status_code} {r.text[:200]}")
    data = r.json()
    message = data.get("message", {})
    body = message.get("body", {})
    lyrics = body.get("lyrics", {})
    text = lyrics.get("lyrics_body", "")
    if not text:
        raise SystemExit("Musixmatch returned empty lyrics_body.")
    marker = "******** This Lyrics is NOT for Commercial use ********"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip()
    log("MM", f"Lyrics fetched in {t1 - t0:.2f}s", GREEN)
    return text


def write_text_file(slug: str, lyrics: str) -> Path:
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    out = TXT_DIR / f"{slug}.txt"
    out.write_text(lyrics, encoding="utf-8")
    log("TXT", f"Wrote lyrics to {out}", GREEN)
    return out


def ytdlp_search_and_download(artist: str, title: str, slug: str) -> Path:
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    out_mp3 = MP3_DIR / f"{slug}.mp3"
    if out_mp3.exists():
        log("MP3", f"Audio file {out_mp3} already exists.", YELLOW)
        ans = input(f"Audio file \"{out_mp3}\" exists. Overwrite / re-download? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            log("MP3", "Keeping existing file.", YELLOW)
            return out_mp3

    query = f"{artist} {title}"
    ytdlp_cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "-o",
        str(out_mp3),
        f"ytsearch1:{query}",
    ]
    log("YTDLP", f"Downloading best match for \"{query}\" → {out_mp3}", CYAN)
    t0 = time.perf_counter()
    subprocess.run(ytdlp_cmd, check=True)
    t1 = time.perf_counter()
    log("YTDLP", f"Downloaded audio in {t1 - t0:.2f}s", GREEN)
    return out_mp3


def write_meta(slug: str, artist: str, title: str, full_title: str) -> Path:
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{slug}.json"
    meta = {
        "slug": slug,
        "artist": artist,
        "title": title,
        "full_title": full_title,
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log("META", f"Wrote metadata to {meta_path}", GREEN)
    return meta_path


def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "darwin":
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return True
        elif sys.platform.startswith("linux"):
            p = subprocess.Popen(["xclip", "-selection", "clipboard"], stdin=subprocess.PIPE)
            p.communicate(text.encode("utf-8"))
            return True
        elif sys.platform.startswith("win"):
            import ctypes

            CF_UNICODETEXT = 13
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            data = text.encode("utf-16-le")
            h_global_mem = kernel32.GlobalAlloc(0x0002, len(data) + 2)
            lp_global_mem = kernel32.GlobalLock(h_global_mem)
            ctypes.cdll.msvcrt.memcpy(lp_global_mem, data, len(data))
            kernel32.GlobalUnlock(h_global_mem)
            if user32.OpenClipboard(None):
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, h_global_mem)
                user32.CloseClipboard()
                return True
            return False
        else:
            return False
    except Exception:
        return False


def suggest_next_command(slug: str) -> None:
    cmd = f"python3 scripts/0_master.py --slug {slug}"
    print()
    print(f"{BOLD}{CYAN}Next suggested command (to continue pipeline):{RESET}")
    print(f"  {BOLD}{cmd}{RESET}")
    ans = input("Copy this command to your clipboard? [y/N]: ").strip().lower()
    if ans in ("y", "yes"):
        if copy_to_clipboard(cmd):
            log("CLIP", "Command copied to clipboard.", GREEN)
        else:
            log("CLIP", "Clipboard copy failed or unsupported on this platform.", YELLOW)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate txt (lyrics) and mp3 from a search query.")
    p.add_argument("query", type=str, help="Search query (artist + title, in any order).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    load_env()
    genius_token = get_env_or_die("GENIUS_ACCESS_TOKEN")
    mm_key = get_env_or_die("MUSIXMATCH_API_KEY")

    query = args.query.strip()
    log("MODE", f"txt+mp3 generation for \"{query}\"", CYAN)

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_genius = ex.submit(genius_search, query, genius_token)
        genius_info = fut_genius.result()
        artist = genius_info["artist"]
        title = genius_info["title"]
        full_title = genius_info["full_title"]

        slug = slugify(title)

        fut_lyrics = ex.submit(
            musixmatch_get_lyrics,
            musixmatch_search_track(artist, title, mm_key),
            mm_key,
        )
        fut_audio = ex.submit(ytdlp_search_and_download, artist, title, slug)

        lyrics = fut_lyrics.result()
        mp3_path = fut_audio.result()

    txt_path = write_text_file(slug, lyrics)
    meta_path = write_meta(slug, artist, title, full_title)

    log("SUMMARY", f"Slug:        {slug}", GREEN)
    log("SUMMARY", f"Lyrics txt:  {txt_path}", GREEN)
    log("SUMMARY", f"Audio mp3:   {mp3_path}", GREEN)
    log("SUMMARY", f"Metadata:    {meta_path}", GREEN)

    suggest_next_command(slug)


if __name__ == "__main__":
    main()

# end of 1_txt_mp3.py
