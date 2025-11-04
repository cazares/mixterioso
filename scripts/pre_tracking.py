#!/usr/bin/env python3
import os
import re
import sys
import time
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

GENIUS_BASE = "https://api.genius.com"
MM_BASE = "https://api.musixmatch.com/ws/1.1"

BASE_DIR = Path(__file__).resolve().parent.parent
TXTS_DIR = BASE_DIR / "txts"
MP3S_DIR = BASE_DIR / "mp3s"
META_DIR = BASE_DIR / "meta"
DOTENV_PATH = BASE_DIR / ".env"

# ANSI colors
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"


def log(section: str, msg: str, color: str = CYAN) -> None:
    print(f"{color}[{section}]{RESET} {msg}")


def load_env_from_dotenv() -> None:
    if not DOTENV_PATH.exists():
        return
    try:
        log("ENV", f"Loading .env from {DOTENV_PATH}", YELLOW)
        with DOTENV_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"{RED}[ENV] Warning: failed to load .env: {e}{RESET}", file=sys.stderr)


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        load_env_from_dotenv()
        value = os.environ.get(name)
    if not value:
        print(f"{RED}[ENV] Error: {name} is not set and not found in .env.{RESET}", file=sys.stderr)
        sys.exit(1)
    return value


def slugify_title(title: str) -> str:
    base = title.strip().lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^\w\-]+", "", base)
    return base or "song"


def confirm_overwrite(path: Path, kind: str) -> bool:
    log("WARN", f'{kind} "{path}" already exists.', YELLOW)
    try:
        ans = input(f'{kind} "{path}" exists. Overwrite / re-download? [y/N]: ').strip().lower()
    except EOFError:
        ans = ""
    if ans == "y":
        log("WARN", f"User chose to overwrite {kind.lower()} at {path}", YELLOW)
        return True
    log("WARN", f"Keeping existing {kind.lower()} at {path}", YELLOW)
    return False


def get_genius_artist_title(query: str):
    token = get_required_env("GENIUS_ACCESS_TOKEN")

    log("GENIUS", f'Searching for: "{query}"', MAGENTA)
    t0 = time.perf_counter()
    r = requests.get(
        f"{GENIUS_BASE}/search",
        params={"q": query},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    hits = data.get("response", {}).get("hits") or []
    if not hits:
        print(f"{RED}[GENIUS] No hits for query: {query}{RESET}", file=sys.stderr)
        sys.exit(2)

    top = hits[0]["result"]
    artist = top["primary_artist"]["name"]
    title = top["title"]
    t1 = time.perf_counter()
    log("GENIUS", f'Matched: "{artist} - {title}" in {t1 - t0:.2f}s', MAGENTA)
    return artist, title, t1 - t0


def get_musixmatch_lyrics(artist: str, title: str) -> tuple[str, float]:
    api_key = get_required_env("MUSIXMATCH_API_KEY")

    log("MM", f'Searching Musixmatch for: "{artist} - {title}"', CYAN)
    t0 = time.perf_counter()
    r = requests.get(
        f"{MM_BASE}/track.search",
        params={
            "q_artist": artist,
            "q_track": title,
            "f_has_lyrics": 1,
            "page_size": 1,
            "s_track_rating": "desc",
            "apikey": api_key,
        },
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    track_list = data.get("message", {}).get("body", {}).get("track_list") or []
    if not track_list:
        print(f"{RED}[MM] No track found for: {artist} - {title}{RESET}", file=sys.stderr)
        sys.exit(3)

    track = track_list[0]["track"]
    track_id = track["track_id"]
    mm_artist = track["artist_name"]
    mm_title = track["track_name"]
    log("MM", f'Chosen track: "{mm_artist} - {mm_title}" (track_id={track_id})', CYAN)

    log("MM", f"Fetching lyrics for track_id={track_id}", CYAN)
    r2 = requests.get(
        f"{MM_BASE}/track.lyrics.get",
        params={"track_id": track_id, "apikey": api_key},
        timeout=10,
    )
    r2.raise_for_status()
    body = r2.json().get("message", {}).get("body", {})
    lyrics = body.get("lyrics", {}).get("lyrics_body")
    if not lyrics:
        print(f"{RED}[MM] No lyrics returned for track_id={track_id}{RESET}", file=sys.stderr)
        sys.exit(4)

    t1 = time.perf_counter()
    log("MM", f"Lyrics fetched in {t1 - t0:.2f}s", CYAN)
    return lyrics, t1 - t0


def download_youtube_audio(artist: str, title: str, slug: str) -> tuple[Path, float]:
    query = f"{artist} {title}"
    MP3S_DIR.mkdir(parents=True, exist_ok=True)
    target = MP3S_DIR / f"{slug}.mp3"
    output_template = str(MP3S_DIR / f"{slug}.%(ext)s")

    log("YT", f'Starting yt-dlp search/download for "{query}" → {slug}.mp3', YELLOW)
    t0 = time.perf_counter()
    try:
        subprocess.run(
            [
                "yt-dlp",
                "-x",
                "--audio-format",
                "mp3",
                "--no-playlist",
                "-o",
                output_template,
                f"ytsearch1:{query}",
            ],
            check=True,
        )
    except FileNotFoundError:
        print(f"{RED}[YT] Error: yt-dlp not found in PATH.{RESET}", file=sys.stderr)
        sys.exit(5)
    except subprocess.CalledProcessError as e:
        print(f"{RED}[YT] Error: yt-dlp failed with code {e.returncode}.{RESET}", file=sys.stderr)
        sys.exit(6)

    t1 = time.perf_counter()
    log("YT", f"yt-dlp finished in {t1 - t0:.2f}s", YELLOW)
    return target, t1 - t0


def write_meta(slug: str, artist: str, title: str, query: str) -> None:
    META_DIR.mkdir(parents=True, exist_ok=True)
    meta_path = META_DIR / f"{slug}.json"
    data = {"slug": slug, "artist": artist, "title": title, "query": query}
    meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log("META", f"Saved metadata to {meta_path}", GREEN)


def suggest_tracking_command(slug: str) -> None:
    cmd = (
        f"python3 scripts/tracking.py "
        f"--txt txts/{slug}.txt "
        f"--mp3 mp3s/{slug}.mp3"
    )

    print()
    print(f"{BOLD}[NEXT]{RESET} Suggested tracking command:\n")
    print(cmd)
    print()

    try:
        ans = input("Copy this command to your clipboard? [y/N]: ").strip().lower()
    except EOFError:
        ans = ""

    if ans == "y":
        try:
            subprocess.run(["pbcopy"], input=cmd, text=True, check=True)
            log("NEXT", "Command copied to clipboard via pbcopy.", GREEN)
        except FileNotFoundError:
            print(f"{RED}[NEXT] pbcopy not found; cannot copy to clipboard.{RESET}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"{RED}[NEXT] pbcopy failed with code {e.returncode}.{RESET}", file=sys.stderr)


def parse_args(argv):
    if len(argv) < 1:
        print(f"usage: pre_tracking.py <search query>", file=sys.stderr)
        sys.exit(1)
    query = " ".join(argv)
    return query


def main():
    query = parse_args(sys.argv[1:])
    log("MODE", f'Pre-tracking (parallel) for "{query}"', BOLD)

    t0_total = time.perf_counter()
    artist, title, t_genius = get_genius_artist_title(query)
    slug = slugify_title(title)
    log("SLUG", f'Title slug: "{slug}"', GREEN)

    # save metadata for downstream (title card)
    write_meta(slug, artist, title, query)

    TXTS_DIR.mkdir(parents=True, exist_ok=True)
    MP3S_DIR.mkdir(parents=True, exist_ok=True)

    lyrics_path = TXTS_DIR / f"{slug}.txt"
    audio_path = MP3S_DIR / f"{slug}.mp3"

    want_lyrics = True
    want_audio = True

    if lyrics_path.exists():
        want_lyrics = confirm_overwrite(lyrics_path, "Lyrics file")
    if audio_path.exists():
        want_audio = confirm_overwrite(audio_path, "Audio file")

    t_par_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        lyrics_start = time.perf_counter()
        fut_lyrics = ex.submit(get_musixmatch_lyrics, artist, title) if want_lyrics else None
        audio_start = time.perf_counter()
        fut_audio = ex.submit(download_youtube_audio, artist, title, slug) if want_audio else None

        if fut_lyrics is not None:
            lyrics, t_lyrics = fut_lyrics.result()
        else:
            lyrics = lyrics_path.read_text(encoding="utf-8") if lyrics_path.exists() else ""
            t_lyrics = 0.0
        lyrics_end = time.perf_counter()

        if fut_audio is not None:
            audio_path, t_yt = fut_audio.result()
        else:
            t_yt = 0.0
        audio_end = time.perf_counter()
    t_par_end = time.perf_counter()

    if want_lyrics or not lyrics_path.exists():
        log("FILE", f"Writing lyrics to {lyrics_path}", GREEN)
        lyrics_path.write_text(lyrics, encoding="utf-8")
    else:
        log("FILE", f"Keeping existing lyrics at {lyrics_path}", GREEN)

    t_end = time.perf_counter()

    print(f'{BOLD}{GREEN}Genius top hit: "{artist} - {title}"{RESET}')
    print(f"{GREEN}Lyrics file: {lyrics_path}{RESET}")
    print(f"{GREEN}Audio file:  {audio_path}{RESET}")

    print("\n[timings]")
    print(f"Genius search:          {t_genius:6.2f} s")
    print(f"Musixmatch lyrics call: {t_lyrics:6.2f} s")
    print(f"yt-dlp download call:   {t_yt:6.2f} s")
    print(f"Parallel block (wall):  {t_par_end - t_par_start:6.2f} s")
    print(f"Total:                  {t_end - t0_total:6.2f} s")

    suggest_tracking_command(slug)


if __name__ == "__main__":
    main()

# end of pre_tracking.py
