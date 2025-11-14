#!/usr/bin/env python3
# app.py — Mixterioso Step-1 API (unified /search + compat routes)
# - Download MP3 from YouTube URL/ID or free-form query
# - Optional lyrics lookup (Genius + Musixmatch)
# - Writes meta/<slug>.json and txts/<slug>.txt when available
# - Serves MP3s via /files/{video_id}.mp3  (Range supported by FileResponse)
from __future__ import annotations

import os
import re
import json
import logging
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any

import requests
from fastapi import FastAPI, HTTPException, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ---------- App & CORS ----------
app = FastAPI(title="Mixterioso — Step-1 MP3 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Config / Paths ----------
BASE_DIR = Path(__file__).resolve().parent
MP3_DIR = BASE_DIR / "mp3s"
TXT_DIR = BASE_DIR / "txts"
META_DIR = BASE_DIR / "meta"

for d in (MP3_DIR, TXT_DIR, META_DIR):
    d.mkdir(parents=True, exist_ok=True)

GENIUS_TOKEN = os.getenv("GENIUS_API_TOKEN") or os.getenv("GENIUS_TOKEN")
MUSIXMATCH_KEY = os.getenv("MUSIXMATCH_API_KEY")

# ---------- Logging ----------
log = logging.getLogger("mixterioso.app")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ---------- Utils ----------
_youtube_id_re = re.compile(r"^[A-Za-z0-9_-]{11}$")


def slugify(text: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
    s = re.sub(r"[\s\-]+", "_", s).strip("_").lower()
    return s or "song"


def youtube_id_from_url(url: str) -> Optional[str]:
    try:
        u = urllib.parse.urlparse(url)
        if u.netloc.endswith("youtube.com"):
            vid = urllib.parse.parse_qs(u.query).get("v", [None])[0]
            if vid and _youtube_id_re.match(vid):
                return vid
        if u.netloc.endswith("youtu.be"):
            vid = u.path.lstrip("/").split("/")[0]
            if vid and _youtube_id_re.match(vid):
                return vid
    except Exception:
        pass
    return None


def kind_of_input(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return "empty"
    if s.startswith(("http://", "https://")):
        return "youtube_url" if youtube_id_from_url(s) else "url_maybe_yt"
    if _youtube_id_re.match(s):
        return "youtube_id"
    return "query"


# ---------- yt-dlp helpers ----------
def _ydl_opts(bitrate_kbps: int) -> Dict[str, Any]:
    # Convert/encode to MP3 at requested bitrate
    return {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "outtmpl": str(MP3_DIR / "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(int(bitrate_kbps)),
            }
        ],
    }


def download_mp3_from_url(youtube_url: str, bitrate_kbps: int = 192) -> Dict[str, Any]:
    """
    Returns dict: { video_id, title, mp3_path, download_url, webpage_url }
    """
    try:
        import yt_dlp  # local runtime dep
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp not installed: {e}")

    opts = _ydl_opts(bitrate_kbps)
    info: Dict[str, Any]
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(youtube_url, download=True)
    vid = info.get("id")
    title = info.get("title") or vid or "unknown"
    mp3_path = MP3_DIR / f"{vid}.mp3"
    if not mp3_path.exists():
        # Some extractors produce .m4a without ffmpeg; guard with helpful error
        raise HTTPException(status_code=500, detail="Expected MP3 not found; is ffmpeg installed?")
    return {
        "video_id": vid,
        "title": title,
        "mp3_path": str(mp3_path),
        "download_url": f"/files/{vid}.mp3",
        "webpage_url": info.get("webpage_url"),
    }


def yt_search_best_url(artist: str, title: str, raw_query: str) -> Dict[str, str]:
    """
    Use yt-dlp's ytsearch to find a good candidate.
    """
    try:
        import yt_dlp
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp not installed: {e}")

    query = f"{artist} - {title}" if artist and title else raw_query
    yq = f"ytsearch1:{query}"
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        res = ydl.extract_info(yq, download=False)
    entry = (res or {}).get("entries") or []
    if not entry:
        raise HTTPException(status_code=404, detail="No YouTube results for query.")
    e0 = entry[0]
    return {"url": e0.get("webpage_url") or e0.get("url") or ""}


# ---------- Lyrics helpers ----------
def genius_search(q: str) -> Optional[Dict[str, Any]]:
    if not GENIUS_TOKEN:
        return None
    try:
        r = requests.get(
            "https://api.genius.com/search",
            params={"q": q},
            headers={"Authorization": f"Bearer {GENIUS_TOKEN}"},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("response", {}).get("hits", [])
        if not hits:
            return None
        song = hits[0]["result"]
        return {
            "song_id": song.get("id"),
            "full_title": song.get("full_title"),
            "title": song.get("title"),
            "artist": (song.get("primary_artist") or {}).get("name"),
        }
    except Exception as e:
        log.warning("Genius lookup failed: %s", e)
        return None


def musixmatch_track(artist: str, title: str) -> Optional[int]:
    if not MUSIXMATCH_KEY or not (artist and title):
        return None
    try:
        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.search",
            params={
                "q_artist": artist,
                "q_track": title,
                "s_track_rating": "desc",
                "page_size": 1,
                "apikey": MUSIXMATCH_KEY,
            },
            timeout=10,
        )
        r.raise_for_status()
        ls = r.json().get("message", {}).get("body", {}).get("track_list", [])
        if not ls:
            return None
        return ls[0]["track"]["track_id"]
    except Exception as e:
        log.warning("Musixmatch track search failed: %s", e)
        return None


def musixmatch_lyrics(track_id: int) -> Optional[str]:
    if not MUSIXMATCH_KEY or not track_id:
        return None
    try:
        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.lyrics.get",
            params={"track_id": track_id, "apikey": MUSIXMATCH_KEY},
            timeout=10,
        )
        r.raise_for_status()
        lyr = (
            r.json()
            .get("message", {})
            .get("body", {})
            .get("lyrics", {})
            .get("lyrics_body")
        )
        if not lyr:
            return None
        # Musixmatch appends a disclaimer; keep it simple for now
        return lyr.strip()
    except Exception as e:
        log.warning("Musixmatch lyrics get failed: %s", e)
        return None


# ---------- API Schemas ----------
class MP3Request(BaseModel):
    youtube_url: str
    bitrate_kbps: Optional[int] = 192


class FlexRequest(BaseModel):
    input: str
    bitrate_kbps: Optional[int] = 192


# ---------- Routes ----------
@app.get("/health")
def health():
    return {"ok": True, "mp3_dir": str(MP3_DIR), "txt_dir": str(TXT_DIR), "meta_dir": str(META_DIR)}


@app.head("/files/{filename}")
def head_file(filename: str = FPath(..., description="File name under mp3s/")):
    p = (MP3_DIR / filename).resolve()
    if MP3_DIR not in p.parents or not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    # Fast path: HEAD returns headers only
    return FileResponse(str(p), media_type="audio/mpeg")


@app.get("/files/{filename}")
def serve_file(filename: str = FPath(..., description="File name under mp3s/")):
    p = (MP3_DIR / filename).resolve()
    if MP3_DIR not in p.parents or not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(p), media_type="audio/mpeg")


# --- Compatibility: POST /mp3 (URL only) ---
@app.post("/mp3")
def mp3_from_url(body: MP3Request):
    url = (body.youtube_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="youtube_url is required")
    dl = download_mp3_from_url(url, int(body.bitrate_kbps or 192))
    return {
        "video_id": dl["video_id"],
        "title": dl["title"],
        "bitrate_kbps": int(body.bitrate_kbps or 192),
        "mp3_path": dl["mp3_path"],
        "download_url": dl["download_url"],
        "webpage_url": dl.get("webpage_url"),
    }


# --- Compatibility: POST /mp3_from_query (query only, returns lyrics too) ---
@app.post("/mp3_from_query")
def mp3_from_query(body: Dict[str, Any]):
    q = (body.get("query") or "").strip()
    kbps = int(body.get("bitrate_kbps") or 192)
    if not q:
        raise HTTPException(status_code=400, detail="query is required")
    meta = genius_search(q) or {}
    artist, title = meta.get("artist", ""), meta.get("title", "")
    mm_id = musixmatch_track(artist, title) if (artist and title) else None
    lyrics_text = musixmatch_lyrics(mm_id) if mm_id else None
    yt = yt_search_best_url(artist, title, q)
    dl = download_mp3_from_url(yt["url"], kbps)

    slug_src = f"{artist}_{title}" if artist and title else q
    slug = slugify(slug_src)
    META_DIR.joinpath(f"{slug}.json").write_text(
        json.dumps(
            {
                "artist": artist,
                "title": title,
                "full_title": meta.get("full_title"),
                "genius_song_id": meta.get("song_id"),
                "musixmatch_track_id": mm_id,
                "youtube_id": dl["video_id"],
                "youtube_url": dl.get("webpage_url"),
                "input": q,
                "lyrics_source": "musixmatch" if lyrics_text else None,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if lyrics_text:
        TXT_DIR.joinpath(f"{slug}.txt").write_text(lyrics_text, encoding="utf-8")

    return {
        "video_id": dl["video_id"],
        "title": dl["title"],
        "bitrate_kbps": kbps,
        "mp3_path": dl["mp3_path"],
        "download_url": dl["download_url"],
        "slug": slug,
        "search_metadata": meta,
        "lyrics_text": lyrics_text,
    }


# --- Unified: POST /search (URL/ID/query) ---
@app.post("/search")
def search_flex(body: FlexRequest):
    raw = (body.input or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="input is required")
    kbps = int(body.bitrate_kbps or 192)
    kind = kind_of_input(raw)

    if kind == "youtube_id":
        yt_url = f"https://www.youtube.com/watch?v={raw}"
        dl = download_mp3_from_url(yt_url, kbps)
        title_guess = dl.get("title") or raw
        meta = genius_search(title_guess) or {}
        artist, title = meta.get("artist", ""), meta.get("title", "")
        mm_id = musixmatch_track(artist, title) if (artist and title) else None
        lyrics_text = musixmatch_lyrics(mm_id) if mm_id else None

    elif kind in ("youtube_url", "url_maybe_yt"):
        dl = download_mp3_from_url(raw, kbps)
        title_guess = dl.get("title") or raw
        meta = genius_search(title_guess) or {}
        artist, title = meta.get("artist", ""), meta.get("title", "")
        mm_id = musixmatch_track(artist, title) if (artist and title) else None
        lyrics_text = musixmatch_lyrics(mm_id) if mm_id else None

    else:  # query
        meta = genius_search(raw) or {}
        artist, title = meta.get("artist", ""), meta.get("title", "")
        full_title = meta.get("full_title") or (f"{artist} - {title}".strip(" -") or raw)
        mm_id = musixmatch_track(artist, title) if (artist and title) else None
        lyrics_text = musixmatch_lyrics(mm_id) if mm_id else None
        yt = yt_search_best_url(artist, title, raw)
        dl = download_mp3_from_url(yt["url"], kbps)
        dl["webpage_url"] = yt.get("url") or dl.get("webpage_url")
        meta.setdefault("full_title", full_title)

    # persist artifacts for downstream steps
    slug_src = f"{artist}_{title}" if (locals().get("artist") and locals().get("title")) else raw
    slug = slugify(slug_src)
    meta_payload = {
        "artist": locals().get("artist"),
        "title": locals().get("title"),
        "full_title": locals().get("meta", {}).get("full_title"),
        "genius_song_id": locals().get("meta", {}).get("song_id"),
        "musixmatch_track_id": locals().get("mm_id"),
        "youtube_id": dl.get("video_id"),
        "youtube_url": dl.get("webpage_url"),
        "input": raw,
        "lyrics_source": "musixmatch" if locals().get("lyrics_text") else None,
    }
    try:
        META_DIR.joinpath(f"{slug}.json").write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to write meta JSON: %s", e)
    if locals().get("lyrics_text"):
        try:
            TXT_DIR.joinpath(f"{slug}.txt").write_text(locals()["lyrics_text"], encoding="utf-8")
        except Exception as e:
            log.warning("Failed to write lyrics TXT: %s", e)

    return {
        "video_id": dl["video_id"],
        "title": dl["title"],
        "bitrate_kbps": kbps,
        "mp3_path": dl["mp3_path"],
        "download_url": dl["download_url"],
        "slug": slug,
        "search_metadata": meta_payload,
        "lyrics_text": locals().get("lyrics_text"),
    }
# end of app.py
