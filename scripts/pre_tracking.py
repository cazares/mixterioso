#!/usr/bin/env python3
import os
import re
import sys
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

GENIUS_BASE = "https://api.genius.com"
MM_BASE = "https://api.musixmatch.com/ws/1.1"

# scripts/ -> project root
BASE_DIR = Path(__file__).resolve().parent.parent
TXTS_DIR = BASE_DIR / "txts"
MP3S_DIR = BASE_DIR / "mp3s"
DOTENV_PATH = BASE_DIR / ".env"


def load_env_from_dotenv() -> None:
    """Load .env in project root into os.environ if present."""
    if not DOTENV_PATH.exists():
        return
    try:
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
        print(f"Warning: failed to load .env: {e}", file=sys.stderr)


def get_required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        load_env_from_dotenv()
        value = os.environ.get(name)
    if not value:
        print(f"Error: {name} is not set and not found in .env.", file=sys.stderr)
        sys.exit(1)
    return value


def slugify_title(title: str) -> str:
    base = title.strip()
    base = re.sub(r"\s+", "_", base)          # spaces -> underscores
    base = re.sub(r"[^\w\-]+", "", base)      # remove weird chars
    return base or "song"


def get_genius_artist_title(query: str):
    token = get_required_env("GENIUS_ACCESS_TOKEN")

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
        print(f"No Genius hits for query: {query}", file=sys.stderr)
        sys.exit(2)

    top = hits[0]["result"]
    artist = top["primary_artist"]["name"]
    title = top["title"]
    return artist, title


def get_musixmatch_lyrics(artist: str, title: str) -> str:
    api_key = get_required_env("MUSIXMATCH_API_KEY")

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
        print(f"No Musixmatch track found for: {artist} - {title}", file=sys.stderr)
        sys.exit(3)

    track = track_list[0]["track"]
    track_id = track["track_id"]

    r2 = requests.get(
        f"{MM_BASE}/track.lyrics.get",
        params={"track_id": track_id, "apikey": api_key},
        timeout=10,
    )
    r2.raise_for_status()
    body = r2.json().get("message", {}).get("body", {})
    lyrics = body.get("lyrics", {}).get("lyrics_body")
    if not lyrics:
        print("No lyrics returned from Musixmatch.", file=sys.stderr)
        sys.exit(4)

    return lyrics


def download_youtube_audio(artist: str, title: str, slug: str) -> Path:
    query = f"{artist} {title}"
    MP3S_DIR.mkdir(parents=True, exist_ok=True)
    output_template = str(MP3S_DIR / f"{slug}.%(ext)s")

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
        print("Error: yt-dlp not found in PATH.", file=sys.stderr)
        sys.exit(5)
    except subprocess.CalledProcessError as e:
        print(f"Error: yt-dlp failed with code {e.returncode}.", file=sys.stderr)
        sys.exit(6)

    return MP3S_DIR / f"{slug}.mp3"


def run_sequential(query: str):
    t0 = time.perf_counter()
    artist, title = get_genius_artist_title(query)
    t1 = time.perf_counter()

    slug = slugify_title(title)

    lyrics = get_musixmatch_lyrics(artist, title)
    t2 = time.perf_counter()

    TXTS_DIR.mkdir(parents=True, exist_ok=True)
    lyrics_path = TXTS_DIR / f"{slug}.txt"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    t3 = time.perf_counter()

    audio_path = download_youtube_audio(artist, title, slug)
    t4 = time.perf_counter()

    print(f'Genius top hit: "{artist} - {title}"')
    print(f"Wrote lyrics to {lyrics_path}")
    print(f"Downloaded audio to {audio_path}")

    print("\n[timings sequential]")
    print(f"Genius search:     {t1 - t0:6.2f} s")
    print(f"Musixmatch lyrics: {t2 - t1:6.2f} s")
    print(f"Write txt:         {t3 - t2:6.2f} s")
    print(f"yt-dlp download:   {t4 - t3:6.2f} s")
    print(f"Total:             {t4 - t0:6.2f} s")


def run_parallel(query: str):
    t0 = time.perf_counter()
    artist, title = get_genius_artist_title(query)
    t1 = time.perf_counter()

    slug = slugify_title(title)

    t_par_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as ex:
        lyrics_start = time.perf_counter()
        fut_lyrics = ex.submit(get_musixmatch_lyrics, artist, title)
        audio_start = time.perf_counter()
        fut_audio = ex.submit(download_youtube_audio, artist, title, slug)

        lyrics = fut_lyrics.result()
        lyrics_end = time.perf_counter()
        audio_path = fut_audio.result()
        audio_end = time.perf_counter()
    t_par_end = time.perf_counter()

    TXTS_DIR.mkdir(parents=True, exist_ok=True)
    lyrics_path = TXTS_DIR / f"{slug}.txt"
    lyrics_path.write_text(lyrics, encoding="utf-8")
    t_end = time.perf_counter()

    print(f'Genius top hit: "{artist} - {title}"')
    print(f"Wrote lyrics to {lyrics_path}")
    print(f"Downloaded audio to {audio_path}")

    print("\n[timings parallel]")
    print(f"Genius search:          {t1 - t0:6.2f} s")
    print(f"Musixmatch lyrics call: {lyrics_end - lyrics_start:6.2f} s")
    print(f"yt-dlp download call:   {audio_end - audio_start:6.2f} s")
    print(f"Parallel block (wall):  {t_par_end - t_par_start:6.2f} s")
    print(f"Write txt:              {t_end - t_par_end:6.2f} s")
    print(f"Total:                  {t_end - t0:6.2f} s")


def parse_args(argv):
    mode = "parallel"
    query_parts = []
    for arg in argv[1:]:
        if arg == "--sequential":
            mode = "sequential"
        elif arg == "--parallel":
            mode = "parallel"
        else:
            query_parts.append(arg)

    if not query_parts:
        print(f"usage: {argv[0]} [--sequential|--parallel] <search query>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(query_parts)
    return mode, query


def main():
    mode, query = parse_args(sys.argv)

    if mode == "sequential":
        run_sequential(query)
    else:
        run_parallel(query)


if __name__ == "__main__":
    main()
# end of pre_tracking.py
