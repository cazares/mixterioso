#!/usr/bin/env bash
# initialize_environment.sh — unified setup + cleanup for Karaoke Time
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
rm -rf "$HOME/.cache/pip" "$HOME/.cache/torch" "$HOME/.cache/huggingface" "$HOME/.cache/npm" "$HOME/.cache/yarn"
rm -rf output/ separated/ merged_output/ intermediate/ logs/
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

# Step 3: Install dependencies (safe to re-run)
echo -e "${YELLOW}📦 Installing dependencies...${NC}"
python3 -m pip install --upgrade pip
if [ -f requirements.txt ]; then
    echo -e "${GREEN}Using requirements.txt...${NC}"
    python3 -m pip install -r requirements.txt
else
    echo -e "${YELLOW}No requirements.txt found. Installing default set...${NC}"
    python3 -m pip install \
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

# Step 4: Verify binaries and keys
echo -e "${YELLOW}🔍 Verifying environment...${NC}"
if ! command -v ffmpeg &> /dev/null; then
  echo -e "${RED}[fatal] ffmpeg not found. Install it on your system or container (e.g. 'sudo apt-get install ffmpeg').${NC}"
  exit 1
fi
if ! command -v demucs &> /dev/null; then
  echo -e "${RED}[fatal] demucs not found. Check requirements.txt or run: python3 -m pip install demucs${NC}"
  exit 1
fi
if [ ! -f .env ]; then
  echo -e "${YELLOW}[warn] .env file missing. Create one and add your API keys.${NC}"
fi

echo -e "${GREEN}✅ Environment ready.${NC}"

# Optional: auto-activate in future shells.
# If you're using the Codespaces zsh profile that already sources demucs_env,
# you can delete this whole block to avoid double activation.
SHELL_RC=""
if [ -n "${ZSH_VERSION-}" ]; then
  SHELL_RC="$HOME/.zshrc"
elif [ -n "${BASH_VERSION-}" ]; then
  SHELL_RC="$HOME/.bashrc"
else
  SHELL_RC="$HOME/.bashrc"
fi

ACTIVATE_LINE="source \"$PWD/demucs_env/bin/activate\""

if [ -n "$SHELL_RC" ]; then
  if ! grep -qxF "$ACTIVATE_LINE" "$SHELL_RC" 2>/dev/null; then
    echo "$ACTIVATE_LINE" >> "$SHELL_RC"
    echo -e "${YELLOW}Added auto-activate line to $SHELL_RC${NC}"
  fi
fi

echo -e "${BLUE}To activate later, run:${NC} source demucs_env/bin/activate"
