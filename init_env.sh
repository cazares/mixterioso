#!/usr/bin/env bash
# init_env.sh — Karaoke Time environment setup (demucs_env)
# Author: Miguel Cázares

set -e
FORCE_REBUILD=false
if [ "$1" == "--force-rebuild" ]; then
  FORCE_REBUILD=true
  echo "🧹 Force rebuild requested — deleting existing environment"
fi

# ───────────────────────────────────────────────────────────────
# 1. Detect active virtualenv and refuse to nest
# ───────────────────────────────────────────────────────────────
if [ -n "$VIRTUAL_ENV" ]; then
  echo "⚠️  You are already inside a virtual environment:"
  echo "    $VIRTUAL_ENV"
  echo ""
  echo "💡  Please run one of the following before continuing:"
  echo "    deactivate    # if available"
  echo "    exit          # if 'deactivate' fails or not found"
  echo ""
  echo "Then re-run this script:"
  echo "    ./init_env.sh $1"
  exit 1
fi

# ───────────────────────────────────────────────────────────────
# 2. Create or rebuild demucs_env
# ───────────────────────────────────────────────────────────────
if $FORCE_REBUILD && [ -d "demucs_env" ]; then
  rm -rf demucs_env
fi

if [ ! -d "demucs_env" ]; then
  echo "▶ Creating virtual environment: demucs_env"
  python3 -m venv demucs_env
else
  echo "▶ Reusing existing virtual environment: demucs_env"
fi

# ───────────────────────────────────────────────────────────────
# 3. Activate environment
# ───────────────────────────────────────────────────────────────
echo "▶ Activating demucs_env"
source demucs_env/bin/activate

# ───────────────────────────────────────────────────────────────
# 4. Install dependencies
# ───────────────────────────────────────────────────────────────
echo "▶ Upgrading pip and essentials"
pip3 install --upgrade pip setuptools wheel

echo "▶ Installing dependencies"
pip3 install \
  demucs ffmpeg-python yt-dlp requests numpy pandas \
  openai-whisper librosa pysrt rich pillow python-dotenv lyricsgenius

# ───────────────────────────────────────────────────────────────
# 5. Verify setup
# ───────────────────────────────────────────────────────────────
echo "▶ Checking ffmpeg"
if ! command -v ffmpeg &>/dev/null; then
  echo "⚙️  Installing ffmpeg..."
  if command -v brew &>/dev/null; then
    brew install ffmpeg
  elif command -v apt-get &>/dev/null; then
    sudo apt-get update -y && sudo apt-get install -y ffmpeg
  else
    echo "⚠️  Please install ffmpeg manually"
  fi
fi

echo "▶ Verifying"
python3 --version
ffmpeg -version | head -n 1
demucs --list-models | grep htdemucs_6s || echo "Demucs models verified"

if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
  [ -n "$GENIUS_TOKEN" ] && echo "▶ Genius token loaded" || echo "ℹ️  No GENIUS_TOKEN found"
else
  echo "ℹ️  No .env file found"
fi

# ───────────────────────────────────────────────────────────────
# 6. Done
# ───────────────────────────────────────────────────────────────
echo "✅ demucs_env ready."
read -r -p "Would you like to copy the activation command to clipboard? [y/N] " reply
if [[ "$reply" =~ ^[Yy]$ ]]; then
  ACTIVATE_CMD="source demucs_env/bin/activate"
  if command -v pbcopy &>/dev/null; then
    echo -n "$ACTIVATE_CMD" | pbcopy
    echo "📋 Copied to clipboard (macOS)"
  elif command -v xclip &>/dev/null; then
    echo -n "$ACTIVATE_CMD" | xclip -selection clipboard
    echo "📋 Copied to clipboard (Linux xclip)"
  elif command -v wl-copy &>/dev/null; then
    echo -n "$ACTIVATE_CMD" | wl-copy
    echo "📋 Copied to clipboard (Wayland)"
  else
    echo "⚠️  Clipboard utility not found. Please copy manually:"
    echo "    $ACTIVATE_CMD"
  fi
  echo "💡 Then run it in your terminal to activate:"
  echo "    $ACTIVATE_CMD"
else
  echo "ℹ️  Skipped clipboard copy. You can activate manually with:"
  echo "    source demucs_env/bin/activate"
fi
