#!/usr/bin/env bash
# create + bootstrap ONE master env for karaoke / lyrics

set -e

ENV_NAME="master_karaoke_env"
PYTHON_BIN="python3"

echo "[+] creating venv: ${ENV_NAME}"
${PYTHON_BIN} -m venv "${ENV_NAME}"

echo "[+] upgrading pip"
source "${ENV_NAME}/bin/activate"
pip3 install --upgrade pip

echo "[+] installing core deps..."
pip3 install \
  requests \
  python-dotenv \
  beautifulsoup4 \
  lxml \
  youtube-transcript-api \
  rapidfuzz

echo
echo "[✓] done."
echo
echo "To use it in this shell, run:"
echo "    source ${ENV_NAME}/bin/activate"
echo
echo "Then run your script, e.g.:"
echo "    python3 scripts/auto_lyrics_fetcher.py --artist \"Jesus Adrian Romero\" --title \"Me Dice Que Me Ama\""
# end of scripts/setup_master_env.sh