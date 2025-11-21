#!/usr/bin/env python3
# scripts/2_download.py
#
# Three independent tasks:
#   --task lyrics   (requires --query)
#   --task meta     (requires --slug OR --query)
#   --task mp3      (requires --slug)
#
# FINAL LINE: JSON result only
#
# NOTE: Web scraping is intentionally disabled. Only structured APIs (YouTube metadata,
# Musixmatch, Genius) are allowed. If we ever touch this file again, scraping must stay OFF.

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
# LOAD ENV
# ======================================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

RESET="\033[0m"
CYAN="\033[36m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
BLUE="\033[34m"

BASE = REPO_ROOT
TXT_DIR   = BASE / "txts"
MP3_DIR   = BASE / "mp3s"
META_DIR  = BASE / "meta"
TMP_DIR   = BASE / "tmp"

TXT_DIR.mkdir(exist_ok=True)
MP3_DIR.mkdir(exist_ok=True)
META_DIR.mkdir(exist_ok=True)
TMP_DIR.mkdir(exist_ok=True)

# ======================================================================
# LOG
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
# CLEAN YT TITLE
# ======================================================================
def clean_youtube_title(artist, title):
    if not title:
        return artist, title

    title = re.sub(r"\(.*?official.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?music video.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?video.*?\)", "", title, flags=re.I)
    title = re.sub(r"\(.*?lyrics.*?\)", "", title, flags=re.I)
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"\bHD\b", "", title, flags=re.I)
    title = re.sub(r"\b4K\b", "", title, flags=re.I)
    title = re.sub(r"remaster(ed)?", "", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip()
    return artist.strip(), title.strip()

# ======================================================================
# MUSIXMATCH
# ======================================================================
def mm_search(artist, title, key):
    import urllib.parse, urllib.request
    params = {
        "q_track": title,
        "q_artist": artist,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": key,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lst = data.get("message", {}).get("body", {}).get("track_list", [])
        if lst:
            return lst[0]["track"]["track_id"]
    except:
        return None
    return None

def mm_search_track_only(title, key):
    import urllib.parse, urllib.request
    params = {
        "q_track": title,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": key,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lst = data.get("message", {}).get("body", {}).get("track_list", [])
        if lst:
            return lst[0]["track"]["track_id"]
    except:
        return None
    return None

def mm_search_artist_only(artist, key):
    import urllib.parse, urllib.request
    params = {
        "q_artist": artist,
        "page_size": 1,
        "s_track_rating": "desc",
        "apikey": key,
    }
    url = "https://api.musixmatch.com/ws/1.1/track.search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lst = data.get("message", {}).get("body", {}).get("track_list", [])
        if lst:
            return lst[0]["track"]["track_id"]
    except:
        return None
    return None

def mm_lyrics(track_id, key):
    import urllib.parse, urllib.request
    url = (
        "https://api.musixmatch.com/ws/1.1/track.lyrics.get?"
        + urllib.parse.urlencode({"track_id": track_id, "apikey": key})
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        lyr = data.get("message", {}).get("body", {}).get("lyrics", {})
        text = lyr.get("lyrics_body")
        if text:
            return re.sub(r"\*\*\*.+", "", text).strip()
    except:
        return None
    return None

# ======================================================================
# GENIUS
# ======================================================================
def genius_search(query, token):
    import requests
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get("https://api.genius.com/search", headers=headers, params={"q": query}, timeout=10)
        data = r.json()
        hits = data.get("response", {}).get("hits", [])
        if hits:
            s = hits[0]["result"]
            return s["primary_artist"]["name"], s["title"]
    except:
        return None, None
    return None, None

# ======================================================================
# YOUTUBE METADATA
# ======================================================================
def youtube_metadata(query):
    cmd = ["yt-dlp", "--dump-json", f"ytsearch1:{query}"]
    try:
        raw = subprocess.check_output(cmd, text=True)
        data = json.loads(raw)
    except:
        return None, None

    artist = data.get("artist")
    track  = data.get("track")
    if artist and track:
        return artist.strip(), track.strip()

    title_raw = data.get("title", "")
    if "-" in title_raw:
        p = title_raw.split("-", 1)
        return p[0].strip(), p[1].strip()

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

    MM = os.getenv("MUSIXMATCH_API_KEY")
    GEN = os.getenv("GENIUS_ACCESS_TOKEN")

    # ---------------------------------------------------------------
    # LYRICS TASK
    # ---------------------------------------------------------------
    if task == "lyrics":
        if not query:
            print(json.dumps({"ok": False, "error": "missing-query"}))
            sys.exit(1)

        log("Lyrics", f"Searching lyrics for query: {query}")

        # attempt A — YouTube → MM (artist+title)
        yta, ytt = youtube_metadata(query)
        if yta and ytt:
            artist, title = clean_youtube_title(yta, ytt)
            log("Lyrics", f"YouTube metadata → {artist} - {title}", YELLOW)

            if MM:
                tid = mm_search(artist, title, MM)
                if tid:
                    log("Lyrics", f"Musixmatch track_id={tid}", GREEN)
                    lyr = mm_lyrics(tid, MM)
                    if lyr:
                        out = TXT_DIR / f"{slug}.txt"
                        out.write_text(lyr, encoding="utf-8")
                        log("Lyrics", f"Lyrics saved → {out}", GREEN)
                        print(json.dumps({"ok": True, "slug": slug, "lyrics_path": str(out)}))
                        return

        # attempt B — MM track-only
        if MM:
            if ytt:
                tid = mm_search_track_only(ytt, MM)
                if tid:
                    log("Lyrics", f"Fallback (track-only) MM track_id={tid}", BLUE)
                    lyr = mm_lyrics(tid, MM)
                    if lyr:
                        out = TXT_DIR / f"{slug}.txt"
                        out.write_text(lyr, encoding="utf-8")
                        log("Lyrics", f"Lyrics saved → {out}", GREEN)
                        print(json.dumps({"ok": True, "slug": slug, "lyrics_path": str(out)}))
                        return

        # attempt C — MM artist-only
        if MM:
            if yta:
                tid = mm_search_artist_only(yta, MM)
                if tid:
                    log("Lyrics", f"Fallback (artist-only) MM track_id={tid}", BLUE)
                    lyr = mm_lyrics(tid, MM)
                    if lyr:
                        out = TXT_DIR / f"{slug}.txt"
                        out.write_text(lyr, encoding="utf-8")
                        log("Lyrics", f"Lyrics saved → {out}", GREEN)
                        print(json.dumps({"ok": True, "slug": slug, "lyrics_path": str(out)}))
                        return

        # attempt D — Genius-first metadata → MM again
        if GEN:
            ga, gt = genius_search(query, GEN)
            if ga and gt and MM:
                log("Lyrics", f"Genius metadata → {ga} - {gt}", CYAN)
                tid = mm_search(ga, gt, MM)
                if tid:
                    lyr = mm_lyrics(tid, MM)
                    if lyr:
                        out = TXT_DIR / f"{slug}.txt"
                        out.write_text(lyr, encoding="utf-8")
                        log("Lyrics", f"Lyrics saved → {out}", GREEN)
                        print(json.dumps({"ok": True, "slug": slug, "lyrics_path": str(out)}))
                        return

        # HARD FAIL
        log("Lyrics", "All lyric resolution strategies exhausted. FAILING HARD.", RED)
        print(json.dumps({"ok": False, "slug": slug, "error": "lyrics-not-found"}))
        sys.exit(1)

    # ---------------------------------------------------------------
    # META TASK
    # ---------------------------------------------------------------
    if task == "meta":
        if not slug and not query:
            print(json.dumps({"ok": False, "error": "missing-slug-or-query"}))
            return

        artist, title = None, None

        if query:
            a, t = youtube_metadata(query)
            if a and t:
                artist, title = clean_youtube_title(a, t)
                log("Meta", f"YouTube metadata → {artist} - {title}", YELLOW)

        if (not artist or not title) and GEN and query:
            ga, gt = genius_search(query, GEN)
            if ga and gt:
                artist = artist or ga
                title  = title or gt
                log("Meta", f"Genius metadata → {artist} - {title}", BLUE)

        if not artist:
            artist = "Unknown Artist"
        if not title:
            title = slug.replace("_", " ").title()

        mp = META_DIR / f"{slug}.json"
        write_json(mp, {"slug": slug, "artist": artist, "title": title})

        log("Meta", f"Artist={artist}, Title={title}", GREEN)
        print(json.dumps({"ok": True, "slug": slug, "artist": artist, "title": title, "meta_path": str(mp)}))
        return

    # ---------------------------------------------------------------
    # MP3 TASK
    # ---------------------------------------------------------------
    if task == "mp3":
        if not slug:
            print(json.dumps({"ok": False, "error": "missing-slug"}))
            return

        final = MP3_DIR / f"{slug}.mp3"
        if final.exists():
            log("MP3", f"MP3 already exists: {final}", GREEN)
            print(json.dumps({"ok": True, "slug": slug, "mp3_path": str(final), "video_id": None}))
            return

        tmp = TMP_DIR / f"{slug}.mp3"
        cmd = [
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            "-o", str(tmp),
            f"ytsearch1:{slug}"
        ]
        log("MP3", f"Running yt-dlp: {' '.join(cmd)}", BLUE)
        try:
            subprocess.check_call(cmd)
        except:
            log("MP3", "yt-dlp failed", RED)
            print(json.dumps({"ok": False, "error": "mp3-download-failed"}))
            return

        tmp.rename(final)
        log("MP3", f"Downloaded MP3: {final}", GREEN)
        print(json.dumps({"ok": True, "slug": slug, "mp3_path": str(final), "video_id": None}))
        return


if __name__ == "__main__":
    main()
