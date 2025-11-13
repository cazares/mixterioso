#!/usr/bin/env bash
# init_env.sh — bullet-proof env setup for Karaoke Time on macOS / MacinCloud
# Author: Miguel Cázares
# Goals:
#   • Provision Python 3.11 non-interactively (prefers uv; falls back to Homebrew; then pyenv)
#   • Create a robust venv even if ensurepip is broken (uses uv venv when available)
#   • Work around “File name too long” / exec path edge cases via short symlinks
#   • Install ffmpeg (Homebrew) and Python deps (Torch/Torchaudio/Demucs + base)
#   • Re-runnable/idempotent with clear logs and hard failures only when unavoidable

set -euo pipefail

# ========== Styling ==========
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log(){ printf "%b%s%b\n" "$1" "$2" "$NC"; }

# ========== Config ==========
VENV_DIR="demucs_env"
PY_REQ_VERSION="3.11"
SHORT_BIN_DIR="$HOME/.local/py311/bin"     # short path for exec/shebang edge cases
SHORT_PY="$SHORT_BIN_DIR/python3.11"

log "$BLUE" "🎤 Initializing Karaoke Time environment…"

# ========== Helpers ==========
have(){ command -v "$1" >/dev/null 2>&1; }

append_once(){ local line="$1" file="$2"; grep -Fqx "$line" "$file" 2>/dev/null || printf "%s\n" "$line" >> "$file"; }

brewshellenv(){
  if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"
  elif have brew; then eval "$(brew shellenv)"; fi
}

ensure_brew(){
  if have brew; then brewshellenv; return 0; fi
  log "$YELLOW" "📦 Installing Homebrew…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  brewshellenv
  if [ -n "${HOMEBREW_PREFIX:-}" ]; then
    append_once 'eval "$('"$HOMEBREW_PREFIX"'/bin/brew shellenv)"' "$HOME/.zprofile"
    append_once 'eval "$('"$HOMEBREW_PREFIX"'/bin/brew shellenv)"' "$HOME/.bash_profile"
  fi
}

ensure_clt(){
  # Best-effort; pyenv builds need CLT
  if xcode-select -p >/dev/null 2>&1; then return 0; fi
  log "$YELLOW" "⚙️  Installing Command Line Tools (accept GUI prompt if shown)…"
  /usr/bin/xcode-select --install >/dev/null 2>&1 || true
  for _ in $(seq 1 30); do sleep 10; xcode-select -p >/dev/null 2>&1 && break || true; done
}

# ----- uv path helpers -----
uv_bin_path(){
  # uv installs into ~/.local/bin by default
  if [ -x "$HOME/.local/bin/uv" ]; then printf "%s\n" "$HOME/.local/bin/uv"; return 0; fi
  if have uv; then command -v uv; return 0; fi
  return 1
}

ensure_uv(){
  local uv
  uv="$(uv_bin_path || true)"
  if [ -n "$uv" ]; then printf "%s\n" "$uv"; return 0; fi
  log "$YELLOW" "📦 Installing uv (Python + venv manager)…"
  curl -fsSL https://astral.sh/uv/install.sh | sh -s -- --yes
  uv="$(uv_bin_path || true)"
  if [ -z "$uv" ]; then return 1; fi
  # Persist PATH for future shells
  append_once 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zprofile"
  append_once 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bash_profile"
  printf "%s\n" "$uv"
}

# ----- Provision Python 3.11 (uv → brew → pyenv) -----
ensure_python311(){
  # 1) uv (user-space, no admin)
  local uv
  uv="$(ensure_uv || true)"
  if [ -n "$uv" ]; then
    "$uv" python install "$PY_REQ_VERSION" >/dev/null 2>&1 || true
    local py
    py="$("$uv" python find "$PY_REQ_VERSION" 2>/dev/null || true)"
    if [ -n "$py" ] && [ -x "$py" ]; then printf "%s\n" "$py"; return 0; fi
  fi

  # 2) Homebrew python@3.11 (admin may be required on some images)
  if ! have brew; then ( ensure_brew ) || true; brewshellenv || true; fi
  if have brew; then
    export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
    brew install python@3.11 >/dev/null 2>&1 || true
    local opt="$(brew --prefix 2>/dev/null)/opt/python@3.11/bin/python3.11"
    if [ -x "$opt" ]; then printf "%s\n" "$opt"; return 0; fi
    local pre="$(brew --prefix python@3.11 2>/dev/null || true)"
    if [ -n "$pre" ] && [ -x "$pre/bin/python3.11" ]; then printf "%s\n" "$pre/bin/python3.11"; return 0; fi
  fi

  # 3) pyenv fallback (user-space build)
  ensure_clt
  if [ ! -d "$HOME/.pyenv" ]; then
    log "$YELLOW" "📦 Installing pyenv (fallback)…"
    git clone https://github.com/pyenv/pyenv.git "$HOME/.pyenv"
  fi
  export PYENV_ROOT="$HOME/.pyenv"
  export PATH="$PYENV_ROOT/bin:$PATH"
  eval "$(pyenv init -)" || true
  local ver="3.11.9"
  if ! pyenv versions --bare | grep -qx "$ver"; then
    log "$YELLOW" "⏳ Building Python $ver via pyenv (this may take a few minutes)…"
    export PYTHON_CONFIGURE_OPTS="--enable-framework"
    pyenv install -s "$ver"
  fi
  if [ -x "$PYENV_ROOT/versions/$ver/bin/python3.11" ]; then
    printf "%s\n" "$PYENV_ROOT/versions/$ver/bin/python3.11"; return 0
  fi

  return 1
}

shorten_path_for_exec(){
  # Create a short symlink for long/awkward interpreter paths (avoids ENAMETOOLONG on exec/shebang)
  local realbin="$1"
  mkdir -p "$SHORT_BIN_DIR"
  ln -sf "$realbin" "$SHORT_PY"
  case ":$PATH:" in *":$SHORT_BIN_DIR:"*) : ;; *) export PATH="$SHORT_BIN_DIR:$PATH" ;; esac
  printf "%s\n" "$SHORT_PY"
}

py_is_311(){
  local out
  out="$("$1" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo "")"
  [ "$out" = "$PY_REQ_VERSION" ]
}

# ========== Step 0: Ensure Python 3.11 ==========
PYBIN_REAL="$(ensure_python311 || true)"
if [ -z "${PYBIN_REAL:-}" ]; then
  log "$RED" "[fatal] Could not provision Python $PY_REQ_VERSION automatically."
  exit 1
fi

# If executing the real path fails, always use a short symlink wrapper
if ! "$PYBIN_REAL" -V >/dev/null 2>&1; then
  log "$YELLOW" "⚠️  Using shortened path for Python exec."
  PYBIN="$(shorten_path_for_exec "$PYBIN_REAL")"
else
  PYBIN="$PYBIN_REAL"
fi

# Final sanity: must be 3.11.x
if ! py_is_311 "$PYBIN"; then
  # Try short path once more if not already
  if [ "$PYBIN" != "$SHORT_PY" ]; then
    PYBIN="$(shorten_path_for_exec "$PYBIN_REAL")"
  fi
fi
py_is_311 "$PYBIN" || { log "$RED" "[fatal] Resolved interpreter is not Python $PY_REQ_VERSION: $("$PYBIN" -V 2>&1 || echo unknown)"; exit 1; }
log "$GREEN" "Using Python: $PYBIN ($("$PYBIN" -V | awk '{print $2}'))"

# ========== Step 1: Ensure ffmpeg ==========
ensure_ffmpeg(){
  if have ffmpeg; then return 0; fi
  if have brew; then
    log "$YELLOW" "📦 Installing ffmpeg via Homebrew…"
    export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1
    brew install ffmpeg >/dev/null 2>&1 || true
  fi
  have ffmpeg || { log "$RED" "[fatal] ffmpeg not found and could not be auto-installed."; exit 1; }
}
ensure_ffmpeg

# ========== Step 2: Create / Reuse venv (uv preferred) ==========
VENV_REUSED=false
if [ -d "$VENV_DIR" ] && [ -x "$VENV_DIR/bin/python3" ] && "$VENV_DIR/bin/python3" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' | grep -qx "$PY_REQ_VERSION"; then
  VENV_REUSED=true
  log "$GREEN" "Reusing existing venv ($VENV_DIR) with Python $PY_REQ_VERSION."
else
  rm -rf "$VENV_DIR"
  # Prefer uv venv (handles ensurepip issues gracefully)
  if uv_bin_path >/dev/null 2>&1; then
    log "$YELLOW" "🐍 Creating venv with uv…"
    "$(uv_bin_path)" venv --python "$PY_REQ_VERSION" "$VENV_DIR"
  else
    log "$YELLOW" "🐍 Creating venv with python -m venv…"
    set +e
    "$PYBIN" -m venv "$VENV_DIR"
    rc=$?
    set -e
    if [ $rc -ne 0 ]; then
      log "$YELLOW" "[info] venv failed (ensurepip). Retrying with --without-pip + get-pip.py…"
      "$PYBIN" -m venv --without-pip "$VENV_DIR"
      # shellcheck disable=SC1091
      source "$VENV_DIR/bin/activate"
      curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      python3 /tmp/get-pip.py
    fi
  fi
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python3 -m pip install --upgrade pip setuptools wheel

# ========== Step 3: Install dependencies ==========
log "$YELLOW" "📦 Installing Python dependencies…"
if [ -f requirements.txt ]; then
  log "$GREEN" "Using requirements.txt…"
  python3 -m pip install -r requirements.txt
else
  log "$YELLOW" "No requirements.txt found — installing default stack…"
  python3 -m pip install --upgrade soundfile ffmpeg-python tqdm requests python-dotenv openai yt-dlp rich
  python3 - <<'PY'
import subprocess, sys
pkgs = ["torch>=2.1,<3", "torchaudio>=2.1,<3", "demucs"]
print("[info] Installing:", " ".join(pkgs), flush=True)
subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])
PY
fi

# ========== Step 4: Verify tools ==========
log "$YELLOW" "🔍 Verifying environment…"
have ffmpeg || { log "$RED" "[fatal] ffmpeg not found after install."; exit 1; }

python3 - <<'PY'
import shutil, sys
missing=[]
if shutil.which("demucs") is None: missing.append("demucs")
if missing:
    print("[fatal] Missing in venv: " + ", ".join(missing), flush=True); sys.exit(1)
PY

[ -f .env ] || log "$YELLOW" "[warn] .env not found — create one and add your API keys when ready."

log "$GREEN" "✅ Environment ready."
log "$BLUE"  "To activate later:  source ${VENV_DIR}/bin/activate"

# end of init_env.sh
