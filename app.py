#!/usr/bin/env python3
# app.py — hardened Step-1 REST API (YouTube URL → MP3) for MacinCloud or local use

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp

# ========== Config ==========
APP_NAME = "Step1 MP3 API"
MP3_DIR = Path("mp3s")
MP3_DIR.mkdir(parents=True, exist_ok=True)

# ========== Logging ==========
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("step1-api")


# ========== Helpers ==========
SAFE_MP3_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}\.mp3$")  # YouTube IDs are 11 chars; allow a bit wider for safety.

def _ffmpeg_location() -> Optional[str]:
    """
    Return a path usable by yt-dlp's 'ffmpeg_location' option.
    Accepts either a directory containing ffmpeg or the ffmpeg binary itself.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    # yt-dlp accepts a directory; that is slightly more portable across wrappers
    return os.path.dirname(ff)

def _looks_like_mp3(p: Path) -> bool:
    # Basic magic check: 'ID3' (tag) or 0xFF sync word frame header.
    try:
        with p.open("rb") as f:
            head = f.read(3)
        return head.startswith(b"ID3") or head.startswith(b"\xff")
    except Exception:
        return False

def _validate_mp3(path: Path) -> None:
    if not path.exists():
        raise HTTPException(status_code=500, detail="MP3 file missing after processing.")
    if path.stat().st_size < 32_000:  # ~32 KB minimum sanity
        raise HTTPException(status_code=500, detail="MP3 too small; ffmpeg likely failed.")
    if not _looks_like_mp3(path):
        raise HTTPException(status_code=500, detail="Generated file is not a valid MP3.")


# ========== API ==========
app = FastAPI(title=APP_NAME)

# Allow mobile/localhost by default; tighten in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MP3Request(BaseModel):
    youtube_url: HttpUrl
    bitrate_kbps: Optional[int] = 192  # 128/192/256/320


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
    # HEAD: 200 with no body
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
    """
    Download bestaudio from YouTube and convert it to MP3 at the requested bitrate.
    Returns metadata and a local download URL (/files/<video_id>.mp3).
    """
    ff_loc = _ffmpeg_location()
    if not ff_loc:
        # Fast fail with a clear message rather than producing a zero-byte file.
        raise HTTPException(status_code=500, detail="ffmpeg not found on PATH. Install ffmpeg and retry.")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(MP3_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(body.bitrate_kbps or 192),
            }
        ],
        "overwrites": True,
        "ffmpeg_location": ff_loc,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(body.youtube_url), download=True)
    except yt_dlp.utils.DownloadError as e:
        # Common cases: region lock, age restriction, network error, invalid URL
        log.warning("yt-dlp download error: %s", e)
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")
    except Exception as e:
        log.exception("Unexpected error in yt-dlp")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    video_id = info.get("id")
    title = info.get("title") or video_id or "audio"
    mp3_path = MP3_DIR / f"{video_id}.mp3"

    # Some postprocessors can vary the final name; be defensive.
    if not mp3_path.exists():
        matches = list(MP3_DIR.glob(f"{video_id}*.mp3"))
        if matches:
            mp3_path = matches[0]

    _validate_mp3(mp3_path)

    # Serve via /files/<video_id>.mp3
    filename = mp3_path.name
    if not SAFE_MP3_RE.match(filename):
        # Normalize an odd filename to the canonical <id>.mp3
        fixed = MP3_DIR / f"{video_id}.mp3"
        try:
            mp3_path.rename(fixed)
            filename = fixed.name
            mp3_path = fixed
        except Exception:
            # If rename fails, still return the original path safely.
            pass

    return {
        "video_id": video_id,
        "title": title,
        "bitrate_kbps": body.bitrate_kbps or 192,
        "mp3_path": str(mp3_path.resolve()),
        "download_url": f"/files/{filename}",
    }
# end of app.py
