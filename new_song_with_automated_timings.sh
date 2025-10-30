#!/bin/bash
# new_song_with_automated_timings.sh — Master pipeline script (auto: lyrics → yt → align → render)
# Run like:
#   ./new_song_with_automated_timings.sh "Shakira" "Soltera"

set -e

artist="$1"
title="$2"

if [[ -z "$artist" || -z "$title" ]]; then
  echo "Usage: $0 \"Artist Name\" \"Song Title\""
  exit 1
fi

# ✅ IMPORTANT: repo root = folder where THIS script lives
# (not the parent — your project is already in karaoke-time-by-miguel)
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

SONGS_DIR="$REPO_ROOT/songs"
AUTO_LYRICS_DIR="$REPO_ROOT/auto_lyrics"
OUTPUT_DIR="$REPO_ROOT/output"
SCRIPTS_DIR="$REPO_ROOT/scripts"

mkdir -p "$SONGS_DIR" "$AUTO_LYRICS_DIR" "$OUTPUT_DIR"

# slug: "Shakira Soltera" -> "shakira-soltera"
slug="$(echo "${artist} ${title}" | sed -E 's/[^A-Za-z0-9]+/-/g; s/^-+|-+$//g' | tr 'A-Z' 'a-z')"

echo ">>> Fetching lyrics for \"${title}\" by ${artist}..."
lyrics_file="$AUTO_LYRICS_DIR/${slug}.txt"

python3 "$SCRIPTS_DIR/lyrics_fetcher.py" "$artist" "$title" -o "$lyrics_file" >/dev/null 2>&1 || true

if grep -Fxq "Lyrics not found." "$lyrics_file" 2>/dev/null; then
  echo "[ERROR] Lyrics not found for \"$title\" by $artist. Aborting."
  exit 1
fi
echo "[OK] Lyrics saved to $lyrics_file"

echo ">>> Downloading audio from YouTube..."

# prefer scripts/ version
YTPICKER="$SCRIPTS_DIR/youtube_audio_picker.py"
if [[ ! -f "$YTPICKER" ]]; then
  # fallback to root if user drops it there
  YTPICKER="$REPO_ROOT/youtube_audio_picker.py"
fi
if [[ ! -f "$YTPICKER" ]]; then
  echo "[ERROR] youtube_audio_picker.py not found in scripts/ or repo root."
  echo "        Expected: $SCRIPTS_DIR/youtube_audio_picker.py"
  exit 1
fi

# 🚗 call picker in AUTO mode, passing artist + song title
if ! python3 "$YTPICKER" "$artist" "$title"; then
  echo "[ERROR] Failed to download audio for \"$title\". Aborting."
  exit 1
fi

# try to resolve audio file name
audio_file="$SONGS_DIR/auto_${slug}.mp3"
if [[ ! -f "$audio_file" ]]; then
  # maybe picker wrote auto-shakira-soltera.mp3
  alt1="$SONGS_DIR/auto-${slug}.mp3"
  if [[ -f "$alt1" ]]; then
    audio_file="$alt1"
  else
    # last resort: newest audio-like file in songs/
    newest="$(ls -t "$SONGS_DIR"/*.{mp3,m4a,opus,webm,mp4} 2>/dev/null | head -n1 || true)"
    if [[ -n "$newest" && -f "$newest" ]]; then
      audio_file="$newest"
    else
      echo "[ERROR] Audio download step completed but no audio file was found in $SONGS_DIR"
      exit 1
    fi
  fi
fi
echo "[OK] Audio saved to $audio_file"

echo ">>> Aligning lyrics with audio (Whisper model)..."
timings_file="$AUTO_LYRICS_DIR/${slug}.csv"
align_script="$SCRIPTS_DIR/align_to_csv.py"
if [[ ! -f "$align_script" ]]; then
  echo "[ERROR] $align_script not found."
  exit 1
fi

align_models=( "large-v3" "medium" "small" "tiny" )
align_success=false
for model in "${align_models[@]}"; do
  echo " - Trying model: $model"
  if python3 "$align_script" --audio "$audio_file" --lyrics "$lyrics_file" --out "$timings_file" --model "$model"; then
    align_success=true
    break
  else
    echo "   [WARN] $model failed, trying next..."
  fi
done

if ! $align_success; then
  echo "[ERROR] All alignment models failed."
  exit 1
fi
echo "[OK] Alignment CSV: $timings_file"

echo ">>> Rendering karaoke video..."
render_script="$SCRIPTS_DIR/render_from_csv.py"
if [[ ! -f "$render_script" ]]; then
  echo "[ERROR] $render_script not found."
  exit 1
fi

python3 "$render_script" \
  --csv "$timings_file" \
  --audio "$audio_file" \
  --font-size 140 \
  --offset-video -1.0 \
  --append-end-duration 0.0 \
  --repo-root "$REPO_ROOT" \
  --no-open

echo "[DONE] Video should now be in: $OUTPUT_DIR"
# end of new_song_with_automated_timings.sh
