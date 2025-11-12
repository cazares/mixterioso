#!/usr/bin/env bash
# init_env.sh — bullet-proof env setup for Karaoke Time on macOS / MacinCloud
# Author: Miguel Cázares
# Goal:
#   • Auto-install Python 3.11 (via Homebrew; falls back to pyenv if Homebrew unavailable)
#   • Create a robust venv even if ensurepip is broken, then install deps
#   • Auto-install ffmpeg (via Homebrew) if missing
# Usage:
#   ./init_env.sh
# Notes:
#   - Re-runnable/idempotent: will reuse a healthy 3.11 venv; otherwise it rebuilds.
#   - Zero manual steps; prompts only if CLT/brew installation requires OS interaction.

set -euo pipefail

# ========== Styling ==========
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log(){ printf "%b%s%b\n" "$1" "$2" "$NC"; }

log "$BLUE" "🎤 Initializing Karaoke Time environment…"

# ========== Config ==========
VENV_DIR="demucs_env"
PY_REQ_MAJOR=3
PY_REQ_MINOR=11
PY_REQ_VERSION="${PY_REQ_MAJOR}.${PY_REQ_MINOR}"
ARCH="$(uname -m)"
OSX_VER="$(sw_vers -productVersion || echo "unknown")"

# ========= Helpers =========
have(){ command -v "$1" >/dev/null 2>&1; }

append_once(){
  # append_once "line" "file"
  local line="$1" file="$2"
  grep -Fqx "$line" "$file" 2>/dev/null || printf "%s\n" "$line" >> "$file"
}

brewshellenv(){
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  elif have brew; then
    eval "$(brew shellenv)"
  fi
}

ensure_clt(){
  # Try to ensure Command Line Tools (needed for pyenv builds); non-interactive best effort.
  if xcode-select -p >/dev/null 2>&1; then return 0; fi
  log "$YELLOW" "⚙️  Command Line Tools not found. Attempting install (may show a GUI)…"
  if /usr/bin/xcode-select --install >/dev/null 2>&1; then
    # wait up to ~5 minutes for install to complete (poll every 10s)
    for _ in {1..30}; do
      sleep 10
      xcode-select -p >/dev/null 2>&1 && return 0 || true
    done
  fi
  # If still missing, we continue; pyenv build may fail and we’ll error with guidance.
  return 0
}

ensure_brew(){
  if have brew; then brewshellenv; return 0; fi
  log "$YELLOW" "⚙️  Homebrew not found — installing Homebrew non-interactively…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brewshellenv
  # Persist for future shells
  if [ -n "${HOMEBREW_PREFIX:-}" ]; then
    append_once 'eval "$('"$HOMEBREW_PREFIX"'/bin/brew shellenv)"' "$HOME/.zprofile"
    append_once 'eval "$('"$HOMEBREW_PREFIX"'/bin/brew shellenv)"' "$HOME/.bash_profile"
  fi
}

find_py311_path_from_brew(){
  local prefix
  prefix="$(brew --prefix python@3.11 2>/dev/null || true)"
  if [ -n "$prefix" ] && [ -x "$prefix/bin/python3.11" ]; then
    printf "%s\n" "$prefix/bin/python3.11"; return 0
  fi
  # Sometimes brew symlinks into $(brew --prefix)/opt/python@3.11
  if [ -x "$(brew --prefix 2>/dev/null)/opt/python@3.11/bin/python3.11" ]; then
    printf "%s\n" "$(brew --prefix)/opt/python@3.11/bin/python3.11"; return 0
  fi
  return 1
}

ensure_python311(){
  # 1) Prefer Homebrew python@3.11
  if have brew; then
    local py
    py="$(find_py311_path_from_brew || true)"
    if [ -z "$py" ]; then
      log "$YELLOW" "📦 Installing python@3.11 via Homebrew… (macOS $OSX_VER, $ARCH)"
      # speed up brew
      export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
      brew install python@3.11 || true
      py="$(find_py311_path_from_brew || true)"
    fi
    if [ -n "$py" ]; then
      printf "%s\n" "$py"; return 0
    fi
  fi

  # 2) Fallback to pyenv (user-space)
  ensure_clt
  if [ ! -d "$HOME/.pyenv" ]; then
    log "$YELLOW" "📦 Installing pyenv (user-space fallback)…"
    git clone https://github.com/pyenv/pyenv.git "$HOME/.pyenv"
  fi
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)" || true
  local ver="3.11.9"
  if ! pyenv versions --bare | grep -qx "$ver"; then
    log "$YELLOW" "⏳ Building Python $ver via pyenv (this can take a few minutes)…"
    # Allow build to reuse system SDKs
    export PYTHON_CONFIGURE_OPTS="--enable-framework"
    pyenv install -s "$ver"
  fi
  pyenv shell "$ver" || true
  if [ -x "$PYENV_ROOT/versions/$ver/bin/python3.11" ]; then
    printf "%s\n" "$PYENV_ROOT/versions/$ver/bin/python3.11"; return 0
  fi

  return 1
}

ensure_ffmpeg(){
  if have ffmpeg; then return 0; fi
  if have brew; then
    log "$YELLOW" "📦 Installing ffmpeg via Homebrew…"
    export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
    brew install ffmpeg || true
  fi
  if have ffmpeg; then return 0; fi
  log "$RED" "[fatal] ffmpeg not found and could not be installed automatically."
  log "$YELLOW" "       Please enable Homebrew installs on this MacinCloud account, then re-run."
  exit 1
}

py_minor(){
  "$1" -c 'import sys; print(sys.version_info.minor)'
}

py_major(){
  "$1" -c 'import sys; print(sys.version_info.major)'
}

# ========== Step 0: Ensure a usable Python 3.11 ==========
if ! have brew; then
  # Try to install Homebrew; if it fails (policy), we’ll fall back to pyenv
  ( ensure_brew ) || true
  brewshellenv || true
fi

PYBIN="$(ensure_python311 || true)"
if [ -z "${PYBIN:-}" ]; then
  log "$RED" "[fatal] Could not provision Python ${PY_REQ_VERSION} automatically."
  log "$YELLOW" "       If Homebrew is blocked, allow it or pre-install Python ${PY_REQ_VERSION}, then re-run."
  exit 1
fi

if [ "$(py_major "$PYBIN")" -ne "$PY_REQ_MAJOR" ] || [ "$(py_minor "$PYBIN")" -ne "$PY_REQ_MINOR" ]; then
  log "$RED" "[fatal] Resolved interpreter is not Python ${PY_REQ_VERSION}: $("$PYBIN" -V 2>&1)"
  exit 1
fi

log "$GREEN" "Using Python: $PYBIN ($("$PYBIN" -V | awk '{print $2}'))"

# ========== Step 1: Ensure ffmpeg ==========
ensure_ffmpeg

# ========== Step 2: Create/Reuse venv (robust) ==========
reuse=false
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python3" ]; then
  VENV_PY="$VENV_DIR/bin/python3"
  if [ "$("$VENV_PY" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')" = "$PY_REQ_VERSION" ]; then
    reuse=true
    log "$GREEN" "Reusing existing venv ($VENV_DIR) with Python $PY_REQ_VERSION."
  else
    log "$YELLOW" "Existing venv is not Python $PY_REQ_VERSION — rebuilding…"
    rm -rf "$VENV_DIR"
  fi
fi

if [ "$reuse" = false ]; then
  log "$YELLOW" "🐍 Creating virtual environment ($VENV_DIR)…"
  set +e
  "$PYBIN" -m venv "$VENV_DIR"
  VENV_RC=$?
  set -e
  if [ $VENV_RC -ne 0 ]; then
    log "$YELLOW" "[info] venv failed (ensurepip issue). Retrying with --without-pip + get-pip.py…"
    "$PYBIN" -m venv --without-pip "$VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py
  fi
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if ! have pip3; then
  log "$YELLOW" "[info] pip missing in venv. Bootstrapping…"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py
fi

python3 -m pip install --upgrade pip setuptools wheel

# ========== Step 3: Install dependencies ==========
log "$YELLOW" "📦 Installing dependencies…"
if [ -f requirements.txt ]; then
  log "$GREEN" "Using requirements.txt…"
  python3 -m pip install -r requirements.txt
else
  log "$YELLOW" "No requirements.txt found — installing default stack…"
  # Base libs
  python3 -m pip install --upgrade soundfile ffmpeg-python tqdm requests python-dotenv openai yt-dlp rich
  # Torch / Torchaudio / Demucs (3.11 has wheels; pin to <3 for stability)
  python3 - <<'PY'
import subprocess, sys
pkgs = ["torch>=2.1,<3", "torchaudio>=2.1,<3", "demucs"]
print("[info] Installing:", " ".join(pkgs), flush=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
PY
fi

# ========== Step 4: Verify tools ==========
log "$YELLOW" "🔍 Verifying environment…"

if ! have ffmpeg; then
  log "$RED" "[fatal] ffmpeg still not on PATH after install."
  exit 1
fi

python3 - <<'PY'
import shutil, sys
missing = []
if shutil.which("demucs") is None:
    missing.append("demucs")
if missing:
    print("[fatal] Not found in venv:", ", ".join(missing), flush=True)
    sys.exit(1)
PY

if [ ! -f .env ]; then
  log "$YELLOW" "[warn] .env not found — create one and add your API keys when ready."
fi

log "$GREEN" "✅ Environment ready."
log "$BLUE"  "To activate later:  source ${VENV_DIR}/bin/activate"
# end of init_env.sh
