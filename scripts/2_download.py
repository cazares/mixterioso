#!/usr/bin/env python3
# scripts/2_download.py
#
# Three independent tasks:
#   --task lyrics   (requires --query)
#   --task meta     (requires --slug OR --query)
#   --task mp3      (requires --slug)
#
# FINAL LINE: JSON result only

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# ======================================================================
# LOAD ENV FROM REPO ROOT (NOT CWD)
# ======================================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

RESET = "\033[0m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BLUE = "\033[34m"

BASE = Path(__file__).resolve().parent.parent
TXT_DIR = BASE / "txts"
MP3_DIR = BASE / "mp3s"
META_DIR = BASE / "meta"
TMP_DIR = BASE / "tmp"

TXT_DIR.mkdir(exist_ok=True)
MP3_DIR.mkdir(exist_ok=True)
META_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)


# ======================================================================
# LOGGING
# ======================================================================
def log(section, msg, color=CYAN):
    ts = time.strftime("%H:%M:%S")
    print(f"{color}[{ts}] [{section}]{RESET} {msg}")


# ======================================================================
# HELPERS
# ======================================================================
def slugify(text):
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:200] or "song"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ======================================================================
# CLEAN YOUTUBE TITLES
# ======================================================================
def clean_youtube_title(artist, title):
    """
    Normalize YouTube video titles into clean (artist, title).
    Remove noise like '(Official Video)', '[HD]', 'Remastered', etc.
    """
    if not title:
        return artist, title

    # Remove parentheses phrases
    title = re.sub(r"\(.*?official.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?music video.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?video.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?lyrics.*?\)", "", title, flags=re.I)

    # Remove bracketed noise
    title = re.sub(r"\[.*?\]", "", title)

    # Remove HD/4K/remaster
    title = re.sub(r"\bHD\b", "", title, flags=re.I)
    title = re.sub(r"\b4K\b", "", title, flags=re.I)
    title = re.sub(r"remaster(ed)?", "", title, flags=re.I)

    title = re.sub(r"\s+", " ", title).strip()
    return artist.strip(), title.strip()


# ======================================================================
# MUSIXMATCH
# ======================================================================
def musixmatch_search_track_by_artist_title(artist, title, api_key):
    import urllib.parse
    import urllib.request

    params = {
        "q_track": title,
        "q_artist": artist,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": api_key,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lst = data.get("message", {}).get("body", {}).get("track_list", [])
        if lst:
            return lst[0]["track"]["track_id"]
    except Exception:
        return None
    return None


def musixmatch_fetch_lyrics(track_id, api_key):
    import urllib.parse
    import urllib.request

    url = (
        "https://api.musixmatch.com/ws/1.1/track.lyrics.get?"
        + urllib.parse.urlencode({"track_id": track_id, "apikey": api_key})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lyr = data.get("message", {}).get("body", {}).get("lyrics", {})
        text = lyr.get("lyrics_body")
        if text:
            # Strip Musixmatch footer
            return re.sub(r"\*\*\*.+", "", text).strip()
    except Exception:
        return None
    return None


# ======================================================================
# GENIUS (METADATA ONLY — NO LYRICS)
# ======================================================================
def genius_search(query, token):
    import requests

    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.genius.com/search"
    try:
        r = requests.get(url, headers=headers, params={"q": query}, timeout=10)
        data = r.json()
        hits = data.get("response", {}).get("hits", [])
        if hits:
            s = hits[0]["result"]
            return s["primary_artist"]["name"], s["title"]
    except Exception:
        return None, None
    return None, None


# ======================================================================
# YOUTUBE METADATA (FALLBACK SOURCE)
# ======================================================================
def youtube_metadata(query):
    cmd = ["yt-dlp", "--dump-json", f"ytsearch1:{query}"]
    try:
        raw = subprocess.check_output(cmd, text=True)
        data = json.loads(raw)
    except Exception:
        return None, None

    # Music metadata direct fields
    artist = data.get("artist")
    track = data.get("track")
    if artist and track:
        return artist.strip(), track.strip()

    # Parse title: "Artist - Title"
    title_raw = data.get("title", "")
    if "-" in title_raw:
        p = title_raw.split("-", 1)
        return p[0].strip(), p[1].strip()

    # Fallback uploader + title
    uploader = data.get("uploader")
    if uploader:
        return uploader.strip(), title_raw.strip()

    return None, None


# ======================================================================
# MAIN
# ======================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--query")
    ap.add_argument("--slug")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    task = args.task
    query = args.query
    slug = args.slug or (slugify(query) if query else None)

    MUSIX = os.getenv("MUSIXMATCH_API_KEY")
    GENIUS = os.getenv("GENIUS_ACCESS_TOKEN")

    # ------------------------------------------------------------------
    # LYRICS
    # ------------------------------------------------------------------
    if task == "lyrics":
        if not query:
            print(json.dumps({"ok": False, "error": "missing-query"}))
            sys.exit(1)

        log("Lyrics", f"Searching lyrics for query: {query}")

        artist, title = None, None

        # 1) GENIUS → artist/title (primary metadata source)
        if GENIUS:
            ga, gt = genius_search(query, GENIUS)
            if ga and gt:
                artist, title = ga.strip(), gt.strip()
                log("Lyrics", f"Genius metadata → {artist} - {title}", BLUE)
        else:
            log("Lyrics", "GENIUS_ACCESS_TOKEN not set; skipping Genius metadata.", YELLOW)

        # 2) YouTube metadata fallback if Genius didn't give both
        if not artist or not title:
            yta, ytt = youtube_metadata(query)
            if yta and ytt:
                artist_yt, title_yt = clean_youtube_title(yta, ytt)
                # Only fill what we don't have yet so Genius wins when present
                artist = artist or artist_yt
                title = title or title_yt
                log("Lyrics", f"YouTube metadata → {artist} - {title}", YELLOW)

        # 3) MUSIXMATCH: lyrics source (primary, not fallback)
        if MUSIX and artist and title:
            track_id = musixmatch_search_track_by_artist_title(artist, title, MUSIX)
            if track_id:
                log("Lyrics", f"Musixmatch track_id={track_id}", GREEN)
                lyr = musixmatch_fetch_lyrics(track_id, MUSIX)
                if lyr:
                    out = TXT_DIR / f"{slug}.txt"
                    out.write_text(lyr, encoding="utf-8")
                    log("Lyrics", f"Lyrics saved → {out}", GREEN)
                    print(
                        json.dumps(
                            {
                                "ok": True,
                                "slug": slug,
                                "lyrics_path": str(out),
                            }
                        )
                    )
                    return
                else:
                    log("Lyrics", "Musixmatch: lyrics_body empty or missing.", YELLOW)
            else:
                log("Lyrics", "Musixmatch: No track_id", YELLOW)
        else:
            if not MUSIX:
                log("Lyrics", "MUSIXMATCH_API_KEY not set; cannot fetch lyrics.", RED)
            if not (artist and title):
                log("Lyrics", "No usable artist/title for Musixmatch search.", RED)

        # 4) FAIL HARD if we reach here
        log("Lyrics", "No lyrics found. FAILING HARD.", RED)
        print(json.dumps({"ok": False, "slug": slug, "error": "lyrics-not-found"}))
        sys.exit(1)

    # ------------------------------------------------------------------
    # META
    # ------------------------------------------------------------------
    if task == "meta":
        if not slug and not query:
            print(json.dumps({"ok": False, "error": "missing-slug-or-query"}))
            return

        artist, title = None, None

        # YouTube metadata first
        if query:
            yta, ytt = youtube_metadata(query)
            if yta and ytt:
                artist, title = clean_youtube_title(yta, ytt)
                log("Meta", f"YouTube metadata → {artist} - {title}", YELLOW)

        # Genius second
        if (not artist or not title) and GENIUS and query:
            ga, gt = genius_search(query, GENIUS)
            if ga and gt:
                artist = artist or ga
                title = title or gt
                log("Meta", f"Genius metadata → {artist} - {title}", BLUE)

        # Last fallback
        if not artist:
            artist = "Unknown Artist"
        if not title:
            title = slug.replace("_", " ").title()

        mp = META_DIR / f"{slug}.json"
        write_json(mp, {"slug": slug, "artist": artist, "title": title})

        log("Meta", f"Artist={artist}, Title={title}", GREEN)
        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": slug,
                    "artist": artist,
                    "title": title,
                    "meta_path": str(mp),
                }
            )
        )
        return

    # ------------------------------------------------------------------
    # MP3
    # ------------------------------------------------------------------
    if task == "mp3":
        if not slug:
            print(json.dumps({"ok": False, "error": "missing-slug"}))
            return

        final = MP3_DIR / f"{slug}.mp3"
        if final.exists():
            log("MP3", f"MP3 already exists: {final}", GREEN)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "slug": slug,
                        "mp3_path": str(final),
                        "video_id": None,
                    }
                )
            )
            return

        tmp = TMP_DIR / f"{slug}.mp3"
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            str(tmp),
            f"ytsearch1:{slug}",
        ]
        log("MP3", f"Running yt-dlp: {' '.join(cmd)}", BLUE)
        try:
            subprocess.check_call(cmd)
        except Exception:
            log("MP3", "yt-dlp failed", RED)
            print(json.dumps({"ok": False, "error": "mp3-download-failed"}))
            return

        tmp.rename(final)
        log("MP3", f"Downloaded MP3: {final}", GREEN)
        print(
            json.dumps(
                {
                    "ok": True,
                    "slug": slug,
                    "mp3_path": str(final),
                    "video_id": None,
                }
            )
        )
        return


if __name__ == "__main__":
    main()

# end of scripts/2_download.py
