#!/usr/bin/env bash
# verify_env.sh — sanity check for Karaoke Time environment
# Author: Miguel Cázares

set -e

echo "🧠 Checking virtual environment..."

if [ -z "$VIRTUAL_ENV" ]; then
  echo "❌ Not inside any virtual environment."
  echo "💡 Run: source demucs_env/bin/activate"
  exit 1
fi

ENV_NAME=$(basename "$VIRTUAL_ENV")
if [ "$ENV_NAME" != "demucs_env" ]; then
  echo "⚠️  You are inside '$ENV_NAME', not 'demucs_env'."
  echo "💡 Run: deactivate && source demucs_env/bin/activate"
  exit 1
fi

echo "✅ Environment active: $VIRTUAL_ENV"
echo

echo "🐍 Python binary:"
which python3
echo

echo "📦 Core packages:"
pip3 list | grep -E "demucs|ffmpeg|yt-dlp|whisper|lyricsgenius" || echo "⚠️  Some packages missing"
echo

echo "🎵 Demucs models:"
demucs --list-models | grep htdemucs || echo "⚠️  Demucs not responding properly"
echo

echo "🎬 FFmpeg version:"
ffmpeg -version | head -n 1 || echo "⚠️  FFmpeg not found"
echo

echo "✅ Verification complete."
