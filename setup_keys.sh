#!/usr/bin/env bash
# setup_keys.sh — Manage API keys for Karaoke Time
# Author: Miguel Cázares

set -e

echo "🔐 Karaoke Time API key setup"
echo "──────────────────────────────"

# Ensure .env exists
if [ ! -f ".env" ]; then
  echo "ℹ️  Creating new .env file..."
  echo "YOUTUBE_API_KEY=" > .env
  echo "✅ Created .env"
fi

# Read existing value
YOUTUBE_API_KEY=$(grep "^YOUTUBE_API_KEY=" .env | cut -d'=' -f2-)

# Prompt for YouTube API key
if [ -z "$YOUTUBE_API_KEY" ]; then
  read -r -p "Enter your YouTube API key (leave blank to skip): " NEW_KEY
  if [ -n "$NEW_KEY" ]; then
    sed -i '' "s|^YOUTUBE_API_KEY=.*|YOUTUBE_API_KEY=$NEW_KEY|" .env 2>/dev/null || \
      sed -i "s|^YOUTUBE_API_KEY=.*|YOUTUBE_API_KEY=$NEW_KEY|" .env
    echo "✅ Saved YouTube API key to .env"
  else
    echo "⚠️  Skipped setting key. You can edit .env manually anytime."
  fi
else
  echo "✅ Existing YouTube API key already present in .env"
  read -r -p "Would you like to update it? [y/N] " update
  if [[ "$update" =~ ^[Yy]$ ]]; then
    read -r -p "Enter new YouTube API key: " NEW_KEY
    sed -i '' "s|^YOUTUBE_API_KEY=.*|YOUTUBE_API_KEY=$NEW_KEY|" .env 2>/dev/null || \
      sed -i "s|^YOUTUBE_API_KEY=.*|YOUTUBE_API_KEY=$NEW_KEY|" .env
    echo "✅ Updated YouTube API key"
  else
    echo "ℹ️  Keeping existing key unchanged."
  fi
fi

echo
echo "✅ Key setup complete."
echo "Current .env contents:"
grep -v '^#' .env
