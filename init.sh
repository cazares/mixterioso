#!/usr/bin/env bash
# init.sh — one-shot bootstrap + run for the Step-1 REST API (macOS/MacinCloud or Linux)
# - Creates/reuses venv: demucs_env
# - Installs requirements.txt
# - Ensures ffmpeg
# - Ensures app.py exists (minimal YouTube URL → MP3 API if absent)
# - Starts uvicorn app:app (foreground by default, optional --background)
#
# Usage:
#   chmod +x init.sh
#   ./init.sh                                  # 0.0.0.0:8000 with --reload
#   ./init.sh --host 127.0.0.1 --port 8080 --no-reload --workers 2
#   ./init.sh --shell                          # open an interactive shell inside demucs_env
#   ./init.sh --background                     # run API in background, print PID and HOWTO

set -euo pipefail

# ---------- styling ----------
RESET=$'\033[0m'; BOLD=$'\033[1m'; CYAN=$'\033[36m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; BLUE=$'\033[34m'
say(){ printf "%b%s%b\n" "$1" "$2" "$RESET"; }

# ---------- config ----------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT/demucs_env"
PYBIN="$VENV_DIR/bin/python3"
REQUIREMENTS="$ROOT/requirements.txt"
APP_FILE="$ROOT/app.py"
LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"

HOST="0.0.0.0"
PORT="8000"
RELOAD="1"
WORKERS="0"
APP="app:app"
SHELL_MODE="0"
BACKGROUND="0"

# ---------- arg parse ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2;;
    --no-reload) RELOAD="0"; shift;;
    --workers) WORKERS="${2:-0}"; shift 2;;
    --app) APP="${2:-app:app}"; shift 2;;
    --shell) SHELL_MODE="1"; shift;;
    --background) BACKGROUND="1"; shift;;
    *) say "$YELLOW" "Ignoring unknown arg: $1"; shift;;
  esac
done

have(){ command -v "$1" >/dev/null 2>&1; }

mac_brew(){
  if [[ -x /opt/homebrew/bin/brew ]]; then echo /opt/homebrew/bin/brew; return
  elif [[ -x /usr/local/bin/brew ]]; then echo /usr/local/bin/brew; return
  elif have brew; then command -v brew; return; fi
  echo ""
}

ensure_ffmpeg(){
  if have ffmpeg; then say "$GREEN" "ffmpeg found"; return; fi
  say "$YELLOW" "ffmpeg not found; attempting to install…"
  case "$(uname -s | tr '[:upper:]' '[:lower:]')" in
    darwin)
      local BREW; BREW="$(mac_brew)"
      if [[ -n "$BREW" ]]; then
        HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1 "$BREW" install ffmpeg || true
      fi
      ;;
    linux)
      if have apt-get; then
        sudo apt-get update -y || true
        sudo apt-get install -y ffmpeg || true
      fi
      ;;
  esac
  if ! have ffmpeg; then
    say "$RED" "ffmpeg is required but could not be auto-installed."
    say "$YELLOW" "Install manually, then re-run. On macOS/MacinCloud:  brew install ffmpeg"
    exit 1
  fi
  say "$GREEN" "ffmpeg installed"
}

ensure_venv(){
  if [[ -x "$PYBIN" ]]; then
    say "$GREEN" "Using existing venv: $VENV_DIR"
  else
    rm -rf "$VENV_DIR"
    say "$YELLOW" "Creating venv at $VENV_DIR…"
    python3 -m venv "$VENV_DIR"
    "$PYBIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
    "$PYBIN" -m pip install -U pip setuptools wheel
  fi
}

install_requirements(){
  say "$CYAN" "Installing dependencies from requirements.txt…"
  "$PYBIN" -m pip install --no-cache-dir -r "$REQUIREMENTS"
}

ensure_app_py(){
  if [[ -f "$APP_FILE" ]]; then return; fi
  say "$YELLOW" "app.py not found — creating a minimal Step-1 API (YouTube URL → MP3)…"
  mkdir -p "$ROOT/mp3s"
  cat > "$APP_FILE" <<'PY'
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp

APP_NAME = "Step1 MP3 API"
MP3_DIR = Path("mp3s"); MP3_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title=APP_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class MP3Request(BaseModel):
    youtube_url: HttpUrl
    bitrate_kbps: Optional[int] = 192

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
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(MP3_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": str(body.bitrate_kbps or 192)}
        ],
        "overwrites": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(body.youtube_url), download=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Download failed: {e}")
    vid = info.get("id"); title = info.get("title") or vid
    mp3_path = MP3_DIR / f"{vid}.mp3"
    if not mp3_path.exists():
        matches = list(MP3_DIR.glob(f"{vid}*.mp3"))
        if matches: mp3_path = matches[0]
        else: raise HTTPException(status_code=500, detail="MP3 not found after processing")
    return {"video_id": vid, "title": title, "bitrate_kbps": body.bitrate_kbps or 192, "mp3_path": str(mp3_path.resolve()), "download_url": f"/files/{mp3_path.name}"}
PY
  say "$GREEN" "Created minimal app.py"
}

print_howto(){
  local base="http://$HOST:$PORT"
  cat <<TXT

${BOLD}${BLUE}API READY — quick HOWTO${RESET}
Health:
  curl $base/health

Convert YouTube → MP3:
  curl -X POST $base/mp3 \\
    -H "Content-Type: application/json" \\
    -d '{"youtube_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","bitrate_kbps":192}'

Download MP3 (use video_id from the POST response):
  curl -L $base/files/<video_id>.mp3 -o out.mp3

Static files land in ./mp3s
TXT
}

start_uvicorn_fg(){
  print_howto
  # activate venv for this process; parent shell is unaffected
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  local cmd=(python3 -m uvicorn "$APP" --host "$HOST" --port "$PORT")
  [[ "$RELOAD" == "1" ]] && cmd+=("--reload")
  if [[ "$RELOAD" != "1" && "$WORKERS" != "0" ]]; then cmd+=("--workers" "$WORKERS"); fi
  say "$GREEN" "Starting API at http://$HOST:$PORT  (module: $APP)"
  exec "${cmd[@]}"
}

start_uvicorn_bg(){
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  local cmd=(python3 -m uvicorn "$APP" --host "$HOST" --port "$PORT")
  [[ "$RELOAD" == "1" ]] && cmd+=("--reload")
  if [[ "$RELOAD" != "1" && "$WORKERS" != "0" ]]; then cmd+=("--workers" "$WORKERS"); fi
  nohup "${cmd[@]}" >"$LOG_DIR/api.out" 2>"$LOG_DIR/api.err" &
  local pid=$!
  say "$GREEN" "API started in background (PID $pid) at http://$HOST:$PORT"
  say "$CYAN"  "Logs: $LOG_DIR/api.out  /  $LOG_DIR/api.err"
  print_howto
}

open_shell(){
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  say "$GREEN" "Entering interactive shell inside demucs_env (type 'deactivate' to exit)…"
  exec "${SHELL:-/bin/bash}" -i
}

# ---------- run ----------
ensure_venv
install_requirements
ensure_ffmpeg
ensure_app_py

if [[ "$SHELL_MODE" == "1" ]]; then
  open_shell
elif [[ "$BACKGROUND" == "1" ]]; then
  start_uvicorn_bg
else
  start_uvicorn_fg
fi

# end of init.sh
