#!/usr/bin/env python3
# app.py — Mixterioso Step-1 API (YouTube URL/Query → MP3) with Genius→Musixmatch→YouTube + lyrics

from __future__ import annotations

import logging, os, re, shutil
from pathlib import Path
from typing import Optional, Dict, Any

import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

APP_NAME = "Mixterioso — Step-1 MP3 API"
MP3_DIR = Path("mp3s"); MP3_DIR.mkdir(parents=True, exist_ok=True)
META_DIR = Path("meta"); META_DIR.mkdir(parents=True, exist_ok=True)
TXT_DIR  = Path("txts"); TXT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("mixterioso")

SAFE_MP3_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}\.mp3$")

# ---------- helpers ----------
def _ffmpeg_location() -> Optional[str]:
    ff = shutil.which("ffmpeg")
    return os.path.dirname(ff) if ff else None

def _looks_like_mp3(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            head = f.read(3)
        return head.startswith(b"ID3") or head.startswith(b"\xff")
    except Exception:
        return False

def _validate_mp3(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=500, detail="MP3 file missing after processing.")
    if path.stat().st_size < 32_000:
        raise HTTPException(status_code=500, detail="MP3 too small; ffmpeg likely failed.")
    if not _looks_like_mp3(path):
        raise HTTPException(status_code=500, detail="Generated file is not a valid MP3.")

def slugify(s: str) -> str:
    s = re.sub(r"\s+", "_", s.strip().lower())
    s = re.sub(r"[^\w\-]+", "", s)
    return s or "song"

def _download_mp3_from_url(youtube_url: str, bitrate_kbps: int) -> Dict[str, Any]:
    ff_loc = _ffmpeg_location()
    if not ff_loc:
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH. Install ffmpeg and retry.")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(MP3_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(bitrate_kbps)}
        ],
        "overwrites": True,
        "ffmpeg_location": ff_loc,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(str(youtube_url), download=True)
    vid = info.get("id")
    title = info.get("title") or vid or "audio"
    mp3_path = MP3_DIR / f"{vid}.mp3"
    if not mp3_path.exists():
        matches = list(MP3_DIR.glob(f"{vid}*.mp3"))
        if matches:
            mp3_path = matches[0]
    _validate_mp3(mp3_path)
    filename = mp3_path.name
    if not SAFE_MP3_RE.match(filename):
        try:
            fixed = MP3_DIR / f"{vid}.mp3"
            mp3_path.rename(fixed)
            filename = fixed.name
            mp3_path = fixed
        except Exception:
            pass
    return {
        "video_id": vid,
        "title": title,
        "mp3_path": str(mp3_path.resolve()),
        "download_url": f"/files/{filename}",
        "webpage_url": info.get("webpage_url"),
    }

def _genius_search(query: str) -> Optional[Dict[str, Any]]:
    token = os.getenv("GENIUS_TOKEN")
    if not token:
        return None
    try:
        r = requests.get(
            "https://api.genius.com/search",
            params={"q": query},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        j = r.json()
        hits = (j.get("response", {}) or {}).get("hits", []) or []
        if not hits:
            return None
        res = hits[0]["result"]
        artist = ((res.get("primary_artist") or {}).get("name") or "").strip()
        title = (res.get("title") or "").strip()
        song_id = res.get("id")
        full_title = res.get("full_title") or f"{artist} - {title}"
        return {"artist": artist, "title": title, "song_id": song_id, "full_title": full_title}
    except Exception as e:
        log.warning("Genius lookup failed: %s", e)
        return None

def _musixmatch_track(artist: str, title: str) -> Optional[int]:
    key = os.getenv("MUSIXMATCH_API_KEY")
    if not key or not artist or not title:
        return None
    try:
        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.search",
            params={"q_track": title, "q_artist": artist, "s_track_rating": "desc", "page_size": 1, "apikey": key},
            timeout=15,
        )
        j = r.json()
        lst = ((j.get("message") or {}).get("body") or {}).get("track_list", []) or []
        if not lst:
            return None
        return lst[0]["track"]["track_id"]
    except Exception as e:
        log.warning("Musixmatch track.search failed: %s", e)
        return None

def _musixmatch_lyrics(track_id: int) -> Optional[str]:
    key = os.getenv("MUSIXMATCH_API_KEY")
    if not key or not track_id:
        return None
    try:
        r = requests.get(
            "https://api.musixmatch.com/ws/1.1/track.lyrics.get",
            params={"track_id": track_id, "apikey": key},
            timeout=15,
        )
        j = r.json()
        lyr = ((j.get("message") or {}).get("body") or {}).get("lyrics", {}) or {}
        text = (lyr.get("lyrics_body") or "").strip()
        if not text:
            return None
        # Strip Musixmatch boilerplate if present
        cutoff = "*****"
        if cutoff in text:
            text = text.split(cutoff, 1)[0].rstrip()
        return text
    except Exception as e:
        log.warning("Musixmatch track.lyrics.get failed: %s", e)
        return None

def _yt_search_url(artist: str, title: str, fallback_query: str) -> Dict[str, str]:
    queries = []
    if artist and title:
        queries.append(f"ytsearch1:{artist} - {title} audio")
        queries.append(f"ytsearch1:{artist} {title} audio")
    queries.append(f"ytsearch1:{fallback_query}")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
        for q in queries:
            info = ydl.extract_info(q, download=False)
            entries = info.get("entries") or []
            if entries:
                e = entries[0]
                return {"id": e.get("id"), "url": e.get("webpage_url")}
    raise HTTPException(status_code=404, detail="No YouTube result found for query.")

# ---------- API ----------
app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class MP3Request(BaseModel):
    youtube_url: HttpUrl
    bitrate_kbps: Optional[int] = 192

class QueryRequest(BaseModel):
    query: str
    bitrate_kbps: Optional[int] = 192

@app.get("/health")
def health():
    return {"ok": True, "service": APP_NAME}

@app.head("/files/{filename}")
def head_file(filename: str):
    if not SAFE_MP3_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = MP3_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return Response(status_code=200)

@app.get("/files/{filename}")
def serve_file(filename: str):
    if not SAFE_MP3_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = MP3_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)

@app.post("/mp3")
def create_mp3(body: MP3Request):
    try:
        result = _download_mp3_from_url(str(body.youtube_url), int(body.bitrate_kbps or 192))
        return {
            "video_id": result["video_id"],
            "title": result["title"],
            "bitrate_kbps": body.bitrate_kbps or 192,
            "mp3_path": result["mp3_path"],
            "download_url": result["download_url"],
        }
    except yt_dlp.utils.DownloadError as e:
        log.warning("yt-dlp download error: %s", e)
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")

@app.post("/mp3_from_query")
def create_mp3_from_query(body: QueryRequest):
    q = body.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="query is required")

    # 1) Genius → metadata
    meta = _genius_search(q) or {}
    artist, title = meta.get("artist", ""), meta.get("title", "")
    full_title = meta.get("full_title") or (f"{artist} - {title}".strip(" -") or q)

    # 2) Musixmatch (track id + lyrics)
    mm_track_id = _musixmatch_track(artist, title)
    lyrics_text = _musixmatch_lyrics(mm_track_id) if mm_track_id else None

    # 3) YouTube → pick best candidate
    yt = _yt_search_url(artist, title, q)

    # 4) Download MP3
    dl = _download_mp3_from_url(yt["url"], int(body.bitrate_kbps or 192))

    # 5) Persist metadata + lyrics for pipeline
    slug = slugify(f"{artist}_{title}") if artist and title else slugify(q)
    meta_path = META_DIR / (slug + ".json")
    meta_payload = {
        "artist": artist or None,
        "title": title or None,
        "full_title": full_title,
        "genius_song_id": meta.get("song_id"),
        "musixmatch_track_id": mm_track_id,
        "youtube_id": dl["video_id"],
        "youtube_url": yt.get("url") or dl.get("webpage_url"),
        "query": q,
        "lyrics_source": "musixmatch" if lyrics_text else None,
    }
    try:
        meta_path.write_text(__import__("json").dumps(meta_payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Failed to write meta JSON: %s", e)

    if lyrics_text:
        try:
            (TXT_DIR / f"{slug}.txt").write_text(lyrics_text, encoding="utf-8")
        except Exception as e:
            log.warning("Failed to write lyrics TXT: %s", e)

    return {
        "video_id": dl["video_id"],
        "title": dl["title"],
        "bitrate_kbps": body.bitrate_kbps or 192,
        "mp3_path": dl["mp3_path"],
        "download_url": dl["download_url"],
        "slug": slug,
        "search_metadata": meta_payload,
        "lyrics_text": lyrics_text,  # <= included for the app
    }
# end of app.py
