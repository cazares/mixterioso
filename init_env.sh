#!/usr/bin/env bash
# init_env.sh — unified setup + cleanup for Karaoke Time
# Author: Miguel Cázares
# Purpose: Prepares a clean environment for local or Codespaces use

set -euo pipefail

# ----- Optional Color Output -----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🎤 Initializing Karaoke Time environment...${NC}"

# Step 1: Clean up space first (but keep demucs_env so this is reusable)
echo -e "${YELLOW}🧹 Cleaning previous caches and venvs (without touching demucs_env)...${NC}"
rm -rf .venv venv
rm -rf output/ separated/ merged_output/ intermediate/ logs/

# Drop common caches to save disk
rm -rf \
  "$HOME/.cache/pip" \
  "$HOME/.cache/torch" \
  "$HOME/.cache/huggingface" \
  "$HOME/.cache/npm" \
  "$HOME/.cache/yarn" 2>/dev/null || true

find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Step 2: Create or reuse virtual environment
if [ -d "demucs_env" ]; then
  echo -e "${GREEN}Using existing demucs_env virtual environment...${NC}"
else
  echo -e "${YELLOW}🐍 Creating demucs_env virtual environment...${NC}"
  python3 -m venv demucs_env
fi

# shellcheck source=/dev/null
source demucs_env/bin/activate

# Step 3: Install dependencies (safe to re-run, no pip cache)
echo -e "${YELLOW}📦 Installing dependencies (no pip cache)...${NC}"
python3 -m pip install --upgrade --no-cache-dir pip setuptools wheel

if [ -f requirements.txt ]; then
  echo -e "${GREEN}Using requirements.txt...${NC}"
  python3 -m pip install --no-cache-dir -r requirements.txt
else
  echo -e "${YELLOW}No requirements.txt found. Installing default set...${NC}"
  python3 -m pip install --no-cache-dir \
    soundfile \
    demucs \
    torch \
    torchaudio \
    ffmpeg-python \
    tqdm \
    requests \
    python-dotenv \
    openai
fi

python3 -m pip cache purge >/dev/null 2>&1 || true

# Step 4: Verify binaries and keys
echo -e "${YELLOW}🔍 Verifying environment...${NC}"

# Ensure ffmpeg (install on Debian-based Codespaces if missing)
if ! command -v ffmpeg &> /dev/null; then
  echo -e "${YELLOW}[info] ffmpeg not found, attempting apt install...${NC}"
  if command -v apt-get &> /dev/null; then
    sudo apt-get update -y
    sudo apt-get install -y --no-install-recommends ffmpeg
    sudo rm -rf /var/lib/apt/lists/*
  else
    echo -e "${RED}[fatal] ffmpeg not found and apt-get not available. Install ffmpeg manually.${NC}"
    exit 1
  fi
fi

if ! command -v demucs &> /dev/null; then
  echo -e "${RED}[fatal] demucs not found in venv. Check requirements.txt or run: python3 -m pip install demucs${NC}"
  exit 1
fi

if [ ! -f .env ]; then
  echo -e "${YELLOW}[warn] .env file missing. Create one and add your API keys.${NC}"
fi

echo -e "${GREEN}✅ Environment ready.${NC}"

# Step 5: Auto-activate venv for future shells, using absolute path
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ACTIVATE_LINE="source \"$REPO_ROOT/demucs_env/bin/activate\""

for SHELL_RC in "$HOME/.bashrc" "$HOME/.zshrc"; do
  if [ -w "$SHELL_RC" ] && ! grep -qxF "$ACTIVATE_LINE" "$SHELL_RC" 2>/dev/null; then
    echo "$ACTIVATE_LINE" >> "$SHELL_RC"
    echo -e "${YELLOW}Added auto-activate line to $SHELL_RC${NC}"
  fi
done

echo -e "${BLUE}To activate later, run:${NC} source demucs_env/bin/activate"
