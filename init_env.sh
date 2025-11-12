#!/usr/bin/env bash
# init_env.sh — robust env setup for Karaoke Time on macOS/MacinCloud
# Author: Miguel Cázares
# Goal: Force a Python version Demucs/Torch support (3.11), fix broken ensurepip,
#       and install deps. Use:  ./init_env.sh   (add --auto-install to brew-install py3.11 if missing)

set -euo pipefail

# ===== Styling =====
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log() { printf "%b%s%b\n" "${1}" "${2}" "${NC}"; }

# ===== Config =====
VENV_DIR="demucs_env"
REQUIRED_MINOR=11         # require Python 3.11
AUTO_INSTALL="${1:-}"     # pass --auto-install to brew install python@3.11 if not present

log "$BLUE"  "🎤 Initializing Karaoke Time environment…"

# ---------- Step 0: Locate (or install) Python 3.11 ----------
find_py311() {
  local cands=(
    "python3.11"
    "/opt/homebrew/bin/python3.11"
    "/usr/local/bin/python3.11"
  )
  if command -v brew >/dev/null 2>&1; then
    cands+=("$(brew --prefix)/bin/python3.11")
  fi
  for b in "${cands[@]}"; do
    if command -v "$b" >/dev/null 2>&1; then
      echo "$b"; return 0
    fi
  done
  return 1
}

PYBIN="$(find_py311 || true)"
if [[ -z "${PYBIN:-}" ]]; then
  if [[ "$AUTO_INSTALL" == "--auto-install" ]]; then
    log "$YELLOW" "⚙️  python3.11 not found — installing via Homebrew…"
    if ! command -v brew >/dev/null 2>&1; then
      log "$RED"   "[fatal] Homebrew not found. Install it first from https://brew.sh then re-run with --auto-install."
      exit 1
    fi
    brew install python@3.11
    PYBIN="$(find_py311 || true)"
  fi
fi

if [[ -z "${PYBIN:-}" ]]; then
  log "$RED"   "[fatal] Python 3.11 is required (Torch/Demucs wheels)."
  log "$YELLOW" "        Fix: brew install python@3.11"
  exit 1
fi

PYVER="$("$PYBIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
log "$GREEN" "Using Python: $PYBIN ($PYVER)"

# Safety: refuse 3.13/3.14+ (prevents your current 3.14 failure)
minor="$("$PYBIN" -c 'import sys; print(sys.version_info.minor)')"
if (( minor != REQUIRED_MINOR )); then
  log "$YELLOW" "[warn] You are using Python $PYVER. This script *forces* 3.11 to avoid build failures."
fi

# ---------- Step 1: Clean up ----------
log "$YELLOW" "🧹 Cleaning previous caches and venvs…"
rm -rf "$VENV_DIR" .venv venv
rm -rf "$HOME/.cache/pip" "$HOME/.cache/torch" "$HOME/.cache/huggingface" "$HOME/.cache/npm" "$HOME/.cache/yarn" || true
rm -rf output/ separated/ merged_output/ intermediate/ logs/ || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ---------- Step 2: Create venv (with ensurepip fallback) ----------
log "$YELLOW" "🐍 Creating virtual environment with $PYBIN…"
set +e
"$PYBIN" -m venv "$VENV_DIR"
VENV_RC=$?
set -e

if [[ $VENV_RC -ne 0 ]]; then
  log "$YELLOW" "[info] venv failed (likely ensurepip). Retrying with --without-pip + get-pip.py…"
  "$PYBIN" -m venv --without-pip "$VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py
else
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  if ! command -v pip3 >/dev/null 2>&1; then
    log "$YELLOW" "[info] pip missing in venv. Bootstrapping…"
    curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    python3 /tmp/get-pip.py
  fi
fi

python3 -m pip install --upgrade pip setuptools wheel

# ---------- Step 3: Install dependencies ----------
log "$YELLOW" "📦 Installing dependencies…"
if [[ -f requirements.txt ]]; then
  log "$GREEN" "Using requirements.txt…"
  python3 -m pip install -r requirements.txt
else
  log "$YELLOW" "No requirements.txt found. Installing default set…"
  # Base libs
  python3 -m pip install soundfile ffmpeg-python tqdm requests python-dotenv openai yt-dlp rich
  # Torch/Demucs (3.11 wheels available; avoid 3.14 build-from-source)
  python3 - <<'PY'
import subprocess, sys
print("[info] Installing Torch/Torchaudio/Demucs for Python 3.11…")
subprocess.check_call([sys.executable, "-m", "pip", "install", "torch>=2.1,<3", "torchaudio", "demucs"])
PY
fi

# ---------- Step 4: Verify tools ----------
log "$YELLOW" "🔍 Verifying environment…"
if ! command -v ffmpeg >/dev/null 2>&1; then
  log "$RED"   "[fatal] ffmpeg not found on PATH."
  log "$YELLOW" "        Fix: brew install ffmpeg"
  exit 1
fi

python3 - <<'PY'
import shutil, sys
if shutil.which("demucs") is None:
    print("[fatal] demucs not found in venv. Try: python3 -m pip install demucs", flush=True)
    sys.exit(1)
PY

log "$GREEN" "✅ Environment ready."
log "$BLUE"  "To activate later:  source ${VENV_DIR}/bin/activate"

# end of init_env.sh
