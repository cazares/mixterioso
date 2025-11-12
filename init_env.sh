#!/usr/bin/env bash
# init_env.sh — unified setup + cleanup for Karaoke Time (macOS / MacinCloud)
# Author: Miguel Cázares
# Purpose: Clean install with robust venv creation even when ensurepip is broken; prefers Python <= 3.12 for Torch/Demucs.

set -euo pipefail

# ----- Optional Color Output -----
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
say() { echo -e "$1$2${NC}"; }

say "$BLUE" "🎤 Initializing Karaoke Time environment..."

# ---------- Step 0: choose a Python suitable for Torch/Demucs ----------
choose_python() {
  # Prefer versions with solid Torch wheels; avoid >= 3.13 for now.
  local candidates=(python3.11 python3.12 python3.10 python3)
  for bin in "${candidates[@]}"; do
    if command -v "$bin" >/dev/null 2>&1; then
      # Ensure it's Python 3.x
      if "$bin" -c 'import sys; sys.exit(0 if sys.version_info.major==3 else 1)'; then
        echo "$bin"
        return 0
      fi
    fi
  done
  return 1
}

PYBIN="$(choose_python || true)"
if [[ -z "${PYBIN:-}" ]]; then
  say "$RED"   "[fatal] No Python 3 found on PATH."
  say "$YELLOW" "        Install Python 3.11 and re-run: brew install python@3.11"
  exit 1
fi

PYVER="$("$PYBIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
say "$GREEN" "Using Python: $PYBIN ($PYVER)"

# Warn if base Python is too new for wheels (e.g., 3.13/3.14)
ver_major="${PYVER%%.*}"
ver_minor="$(echo "$PYVER" | cut -d. -f2)"
if (( ver_major == 3 && ver_minor >= 13 )); then
  say "$YELLOW" "[warn] Python $PYVER detected. Torch/Demucs wheels may be unavailable. Prefer python3.11."
fi

# ---------- Step 1: Clean up space first ----------
say "$YELLOW" "🧹 Cleaning previous caches and venvs (without touching demucs_env)..."
# Keep demucs_env if it already exists and user wants to reuse; comment out next line to preserve
rm -rf demucs_env
rm -rf "$HOME/.cache/pip" "$HOME/.cache/torch" "$HOME/.cache/huggingface" "$HOME/.cache/npm" "$HOME/.cache/yarn" || true
rm -rf output/ separated/ merged_output/ intermediate/ logs/ || true
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ---------- Step 2: Create virtual environment (robust, tolerate broken ensurepip) ----------
say "$YELLOW" "🐍 Creating demucs_env virtual environment..."
set +e
"$PYBIN" -m venv demucs_env
VENV_RC=$?
set -e

if [[ $VENV_RC -ne 0 ]]; then
  say "$YELLOW" "[info] python -m venv failed (likely ensurepip). Retrying with --without-pip…"
  "$PYBIN" -m venv --without-pip demucs_env
fi

# shellcheck disable=SC1091
source demucs_env/bin/activate

# If pip is missing inside venv, bootstrap it
if ! command -v pip >/dev/null 2>&1; then
  say "$YELLOW" "[info] Bootstrapping pip inside venv (get-pip.py)…"
  curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py
fi

# Ensure modern packaging tools
python3 -m pip install --upgrade pip setuptools wheel

# ---------- Step 3: Install dependencies ----------
say "$YELLOW" "📦 Installing dependencies..."
if [[ -f requirements.txt ]]; then
  say "$GREEN" "Using requirements.txt…"
  python3 -m pip install -r requirements.txt
else
  say "$YELLOW" "No requirements.txt found. Installing default set…"
  # Base libs
  python3 -m pip install soundfile ffmpeg-python tqdm requests python-dotenv openai yt-dlp rich
  # Torch/Demucs stack (only on <= 3.12)
  python3 - <<'PY'
import sys, subprocess
maj, min = sys.version_info[:2]
if maj == 3 and min <= 12:
    pkgs = ['torch>=2.1,<3', 'torchaudio', 'demucs']
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', *pkgs])
else:
    print("[warn] Skipping torch/torchaudio/demucs: Python {}.{} lacks stable wheels. Use python3.11."
          .format(maj, min))
PY
fi

# ---------- Step 4: Verify binaries and keys ----------
say "$YELLOW" "🔍 Verifying environment…"
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "$RED" "[fatal] ffmpeg not found on PATH. Install it with Homebrew and re-run:"
  say "$YELLOW" "       brew install ffmpeg"
  exit 1
fi

# Verify demucs if Python is compatible
python3 - <<'PY'
import sys, shutil
maj, min = sys.version_info[:2]
if maj == 3 and min <= 12:
    if shutil.which('demucs') is None:
        print("[fatal] demucs not found in venv. Try: python3 -m pip install demucs")
        sys.exit(1)
else:
    print("[warn] Python {}.{} in use; Demucs was not installed. Prefer Python 3.11 for full pipeline."
          .format(maj, min))
PY

if [[ ! -f .env ]]; then
  say "$YELLOW" "[warn] .env file missing. Create one and add your API keys."
fi

say "$GREEN" "✅ Environment ready."
say "$BLUE"  "To activate later, run: source demucs_env/bin/activate"

# end of init_env.sh
