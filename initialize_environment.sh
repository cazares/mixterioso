#!/usr/bin/env bash
# initialize_environment.sh — unified setup + cleanup for Karaoke Time
# Author: Miguel Cázares
# Purpose: Prepares a clean environment for local or Codespaces use

set -e

# ----- Optional Color Output -----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🎤 Initializing Karaoke Time environment...${NC}"

# Step 1: Clean up space first
echo -e "${YELLOW}🧹 Cleaning previous caches and venvs...${NC}"
rm -rf demucs_env .venv venv
rm -rf ~/.cache/pip ~/.cache/torch ~/.cache/huggingface ~/.cache/npm ~/.cache/yarn
rm -rf output/ separated/ merged_output/ intermediate/ logs/
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null

# Step 2: Create virtual environment
echo -e "${YELLOW}🐍 Creating virtual environment...${NC}"
python3 -m venv demucs_env
source demucs_env/bin/activate

# Step 3: Install dependencies
echo -e "${YELLOW}📦 Installing dependencies...${NC}"
pip install --upgrade pip
if [ -f requirements.txt ]; then
    echo -e "${GREEN}Using requirements.txt...${NC}"
    pip install -r requirements.txt
else
    echo -e "${YELLOW}No requirements.txt found. Installing default set...${NC}"
    pip install soundfile demucs torch torchaudio ffmpeg-python tqdm requests python-dotenv openai
fi

# Step 4: Verify binaries and keys
echo -e "${YELLOW}🔍 Verifying environment...${NC}"
if ! command -v ffmpeg &> /dev/null; then
  echo -e "${RED}[fatal] ffmpeg not found. Install it with: brew install ffmpeg${NC}"
  exit 1
fi
if ! command -v demucs &> /dev/null; then
  echo -e "${RED}[fatal] demucs not found in venv. Check requirements.txt or run: pip install demucs${NC}"
  exit 1
fi
if [ ! -f .env ]; then
  echo -e "${YELLOW}[warn] .env file missing. Create one and add your API keys.${NC}"
fi

echo -e "${GREEN}✅ Environment ready.${NC}"
echo -e "${BLUE}To activate later, run:${NC} source demucs_env/bin/activate"
