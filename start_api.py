#!/usr/bin/env python3
# start_api.py — bootstrap + launch the Mixterioso Step-1 REST API (YouTube URL → MP3)
# - Ensures a local venv ./demucs_env exists (creates if missing)
# - Installs requirements.txt (or a minimal API stack if requirements.txt absent)
# - Verifies ffmpeg is available
# - Creates ./app.py if missing (hardened FastAPI app)
# - Starts uvicorn app:app

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / "demucs_env"
PY_IN_VENV = VENV_DIR / "bin" / "python3"  # macOS/Linux layout

APP_FILE = ROOT / "app.py"
MP3_DIR = ROOT / "mp3s"
REQS = ROOT / "requirements.txt"

APP_TITLE = "Mixterioso — Step-1 MP3 API"

RESET = "\033[0m"; BOLD = "\033[1m"; CYAN = "\033[36m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; RED = "\033[31m"

def say(color: str, msg: str) -> None:
    print(f"{color}{msg}{RESET}")

def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None

def in_venv() -> bool:
    return bool(os.environ.get("VIRTUAL_ENV")) and Path(os.environ["VIRTUAL_ENV"]).resolve() == VENV_DIR

def ensure_venv() -> None:
    if PY_IN_VENV.exists():
        say(GREEN, f"Using venv: {VENV_DIR}")
        return
    say(YELLOW, f"Creating venv at {VENV_DIR} …")
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    # ensure pip
    subprocess.run([str(PY_IN_VENV), "-m", "ensurepip", "--upgrade"], check=False)
    subprocess.run([str(PY_IN_VENV), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"], check=False)

def relaunch_inside_venv(argv: list[str]) -> None:
    if in_venv():
        return
    # Relaunch this script with the venv's interpreter
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    env["PATH"] = f"{VENV_DIR}/bin:{env.get('PATH','')}"
    os.execve(str(PY_IN_VENV), [str(PY_IN_VENV), __file__, *argv], env)

def install_requirements(no_install: bool) -> None:
    if no_install:
        say(YELLOW, "Skipping dependency install due to --no-install.")
        return
    if REQS.exists():
        say(CYAN, "Installing dependencies from requirements.txt …")
        subprocess.run([str(PY_IN_VENV), "-m", "pip", "install", "-r", str(REQS)], check=True)
    else:
        say(CYAN, "requirements.txt not found — installing minimal API stack …")
        subprocess.run(
            [str(PY_IN_VENV), "-m", "pip", "install",
             "fastapi", "uvicorn[standard]", "pydantic>=2",
             "python-multipart", "aiofiles", "yt-dlp", "requests", "rich"],
            check=True,
        )

def ensure_ffmpeg() -> None:
    if have("ffmpeg"):
        say(GREEN, "ffmpeg found")
        return
    say(RED, "ffmpeg not found on PATH.")
    say(YELLOW, "Install on macOS:  brew install ffmpeg")
    sys.exit(1)

def ensure_app_py() -> None:
    if APP_FILE.exists():
        return
    say(YELLOW, "app.py not found — creating a hardened minimal API …")
    MP3_DIR.mkdir(parents=True, exist_ok=True)
    APP_FILE.write_text(dedent(f"""
    #!/usr/bin/env python3
    # app.py — {APP_TITLE}

    from __future__ import annotations
    import logging, os, re, shutil
    from pathlib import Path
    from typing import Optional
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, HttpUrl
    import yt_dlp

    APP_NAME = "{APP_TITLE}"
    MP3_DIR = Path("mp3s"); MP3_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("mixterioso")

    SAFE_MP3_RE = re.compile(r"^[A-Za-z0-9_-]{{6,32}}\\.mp3$")

    def _ffmpeg_location() -> Optional[str]:
        ff = shutil.which("ffmpeg")
        return os.path.dirname(ff) if ff else None

    def _looks_like_mp3(p: Path) -> bool:
        try:
            with p.open("rb") as f:
                head = f.read(3)
            return head.startswith(b"ID3") or head.startswith(b"\\xff")
        except Exception:
            return False

    def _validate_mp3(path: Path) -> None:
        if not path.exists(): raise HTTPException(status_code=500, detail="MP3 file missing after processing.")
        if path.stat().st_size < 32000: raise HTTPException(status_code=500, detail="MP3 too small; ffmpeg failed.")
        if not _looks_like_mp3(path): raise HTTPException(status_code=500, detail="Generated file is not a valid MP3.")

    app = FastAPI(title=APP_NAME)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    class MP3Request(BaseModel):
        youtube_url: HttpUrl
        bitrate_kbps: int | None = 192

    @app.get("/health")
    def health():
        return {{"ok": True, "service": APP_NAME}}

    @app.head("/files/{{filename}}")
    def head_file(filename: str):
        if not SAFE_MP3_RE.match(filename): raise HTTPException(status_code=400, detail="Invalid filename.")
        path = MP3_DIR / filename
        if not path.exists(): raise HTTPException(status_code=404, detail="File not found.")
        return Response(status_code=200)

    @app.get("/files/{{filename}}")
    def serve_file(filename: str):
        if not SAFE_MP3_RE.match(filename): raise HTTPException(status_code=400, detail="Invalid filename.")
        path = MP3_DIR / filename
        if not path.exists(): raise HTTPException(status_code=404, detail="File not found.")
        return FileResponse(path, media_type="audio/mpeg", filename=filename)

    @app.post("/mp3")
    def create_mp3(body: MP3Request):
        ff_loc = _ffmpeg_location()
        if not ff_loc:
            raise HTTPException(status_code=500, detail="ffmpeg not found on PATH.")

        ydl_opts = {{
            "format": "bestaudio/best",
            "outtmpl": str(MP3_DIR / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(body.bitrate_kbps or 192)}}],
            "overwrites": True,
            "ffmpeg_location": ff_loc,
        }}

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(str(body.youtube_url), download=True)
        except yt_dlp.utils.DownloadError as e:
            log.warning("yt-dlp error: %s", e)
            raise HTTPException(status_code=400, detail=f"Download failed: {{e}}")
        except Exception as e:
            log.exception("Unexpected error")
            raise HTTPException(status_code=500, detail=f"Unexpected error: {{e}}")

        vid = info.get("id") or "audio"
        title = info.get("title") or vid
        mp3_path = MP3_DIR / f"{{vid}}.mp3"
        if not mp3_path.exists():
            matches = list(MP3_DIR.glob(f"{{vid}}*.mp3"))
            if matches: mp3_path = matches[0]

        _validate_mp3(mp3_path)

        filename = mp3_path.name
        if not SAFE_MP3_RE.match(filename):
            try:
                fixed = MP3_DIR / f"{{vid}}.mp3"
                mp3_path.rename(fixed)
                filename = fixed.name
                mp3_path = fixed
            except Exception:
                pass

        return {{
            "video_id": vid,
            "title": title,
            "bitrate_kbps": body.bitrate_kbps or 192,
            "mp3_path": str(mp3_path.resolve()),
            "download_url": f"/files/{{filename}}",
        }}
    # end of app.py
    """).lstrip(), encoding="utf-8")
    say(GREEN, "Created app.py")

def print_howto(host: str, port: int) -> None:
    base = f"http://{host}:{port}"
    msg = f"""
{BOLD}{CYAN}API READY — Mixterioso HOWTO{RESET}
Health:
  curl {base}/health

Create MP3:
  curl -X POST {base}/mp3 \\
    -H "Content-Type: application/json" \\
    -d '{{"youtube_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","bitrate_kbps":192}}'

Download:
  curl -L {base}/files/<video_id>.mp3 -o out.mp3
"""
    print(msg, flush=True)

def launch_uvicorn(host: str, port: int, reload: bool, workers: int) -> None:
    import uvicorn
    print_howto(host, port)
    kwargs = {"host": host, "port": port, "reload": reload}
    if not reload and workers and workers > 0:
        kwargs["workers"] = int(workers)
    uvicorn.run("app:app", **kwargs)

def parse_args(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description="Start the Mixterioso Step-1 MP3 API")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-reload", action="store_true", help="Disable autoreload (use for production)")
    p.add_argument("--workers", type=int, default=0, help="Uvicorn workers when --no-reload is set")
    p.add_argument("--no-install", action="store_true", help="Skip pip install step")
    return p.parse_args(argv)

def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    say(CYAN, f"{APP_TITLE}")

    ensure_venv()
    relaunch_inside_venv(sys.argv[1:])  # never returns in the parent process

    # From here on we are inside demucs_env
    ensure_ffmpeg()
    install_requirements(no_install=args.no_install)
    ensure_app_py()
    MP3_DIR.mkdir(parents=True, exist_ok=True)

    launch_uvicorn(
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        workers=args.workers,
    )

if __name__ == "__main__":
    main()
# end of start_api.py
