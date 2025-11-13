# app.py
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp

APP_NAME = "Step1 MP3 API"
MP3_DIR = Path("mp3s")
MP3_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_NAME)

# Allow mobile/localhost by default; tighten as needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # set specific origins in production
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

@app.get("/files/{filename}")
def serve_file(filename: str):
    path = MP3_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="audio/mpeg", filename=filename)

@app.post("/mp3")
def create_mp3(body: MP3Request):
    """
    Downloads audio from YouTube URL and converts to MP3 at requested bitrate.
    Returns metadata + a download URL you can hit to stream the file.
    """
    # yt-dlp will first download bestaudio to temp, then ffmpeg postprocess to MP3.
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
        # Ensure ffmpeg is available on PATH. Optionally set "ffmpeg_location" here.
        "overwrites": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(body.youtube_url), download=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")

    video_id = info.get("id")
    title = info.get("title") or video_id
    mp3_path = MP3_DIR / f"{video_id}.mp3"

    if not mp3_path.exists():
        # Fallback in case postprocessor produced a different extension/name
        # (rare with FFmpegExtractAudio, but safe to check).
        matches = list(MP3_DIR.glob(f"{video_id}*.mp3"))
        if matches:
            mp3_path = matches[0]
        else:
            raise HTTPException(status_code=500, detail="MP3 not found after processing")

    return {
        "video_id": video_id,
        "title": title,
        "bitrate_kbps": body.bitrate_kbps or 192,
        "mp3_path": str(mp3_path.resolve()),
        "download_url": f"/files/{mp3_path.name}",
    }
# end of app.py
