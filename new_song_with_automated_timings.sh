#!/usr/bin/env bash
set -euo pipefail

# new_song_with_automated_timings.sh
# Usage:
#   ./new_song_with_automated_timings.sh "Red Hot Chili Peppers" "Californication"
#   ./new_song_with_automated_timings.sh "Red Hot Chili Peppers" "Californication" --font-size 40 --extra-delay 1.0
#   ./new_song_with_automated_timings.sh "Red Hot Chili Peppers" "Californication" --force-align
# Notes:
#   - lyrics: skip if exist (unless --force-lyrics)
#   - audio: skip if exist (unless --force-audio)
#   - csv:   skip if exist (unless --force-align)
#   - render: ALWAYS run (mp4 gets regenerated every time)

if [ $# -lt 2 ]; then
  echo "usage: $0 \"Artist\" \"Title\" [--font-size N] [--force-lyrics] [--force-audio] [--force-align] [--force-render (noop now)] [render extra args...]"
  exit 1
fi

ARTIST="$1"
TITLE="$2"
shift 2 || true

# defaults
FONT_SIZE=40
FORCE_LYRICS=0
FORCE_AUDIO=0
FORCE_ALIGN=0
# kept for compatibility, but we don't actually skip render anymore
FORCE_RENDER=0
EXTRA_RENDER_ARGS=()

# parse optional flags
while [[ $# -gt 0 ]]; do
  case "$1" in
    --font-size)
      FONT_SIZE="$2"
      shift 2
      ;;
    --force-lyrics)
      FORCE_LYRICS=1
      shift 1
      ;;
    --force-audio)
      FORCE_AUDIO=1
      shift 1
      ;;
    --force-align)
      FORCE_ALIGN=1
      shift 1
      ;;
    --force-render)
      FORCE_RENDER=1   # no-op for now, kept for future
      shift 1
      ;;
    *)
      EXTRA_RENDER_ARGS+=("$1")
      shift 1
      ;;
  esac
done

REPO_ROOT="$(pwd)"
SCRIPTS_DIR="$REPO_ROOT/scripts"

# normalize for filenames
SAFE_ARTIST=$(echo "$ARTIST" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
SAFE_TITLE=$(echo "$TITLE" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd '[:alnum:]-')
BASE="${SAFE_ARTIST}-${SAFE_TITLE}"

LYRICS_DIR="$REPO_ROOT/auto_lyrics"
SONGS_DIR="$REPO_ROOT/songs"
OUT_DIR="$REPO_ROOT/output"

mkdir -p "$LYRICS_DIR" "$SONGS_DIR" "$OUT_DIR"

LYRICS_OUT="$LYRICS_DIR/${BASE}.txt"
CSV_OUT="$LYRICS_DIR/${BASE}.csv"
AUDIO_OUT="$SONGS_DIR/auto_${BASE}.mp3"
ASS_OUT="$OUT_DIR/${BASE}.ass"
MP4_OUT="$OUT_DIR/${BASE}.mp4"

# locate helpers
if [ -f "$SCRIPTS_DIR/lyrics_fetcher.py" ]; then
  LYRICS_FETCHER="$SCRIPTS_DIR/lyrics_fetcher.py"
elif [ -f "$REPO_ROOT/lyrics_fetcher.py" ]; then
  LYRICS_FETCHER="$REPO_ROOT/lyrics_fetcher.py"
else
  echo "[ERROR] lyrics_fetcher.py not found."
  exit 1
fi

if [ -f "$SCRIPTS_DIR/youtube_audio_picker.py" ]; then
  YT_PICKER="$SCRIPTS_DIR/youtube_audio_picker.py"
elif [ -f "$REPO_ROOT/youtube_audio_picker.py" ]; then
  YT_PICKER="$REPO_ROOT/youtube_audio_picker.py"
else
  echo "[ERROR] youtube_audio_picker.py not found."
  exit 1
fi

if [ -f "$SCRIPTS_DIR/align_to_csv.py" ]; then
  ALIGNER="$SCRIPTS_DIR/align_to_csv.py"
else
  echo "[ERROR] scripts/align_to_csv.py not found."
  exit 1
fi

if [ -f "$SCRIPTS_DIR/render_from_csv.py" ]; then
  RENDERER="$SCRIPTS_DIR/render_from_csv.py"
else
  echo "[ERROR] scripts/render_from_csv.py not found."
  exit 1
fi

# 1) lyrics
if [ $FORCE_LYRICS -eq 0 ] && [ -s "$LYRICS_OUT" ]; then
  echo "[SKIP] Lyrics already exist at $LYRICS_OUT"
else
  echo ">>> Fetching lyrics for \"$TITLE\" by $ARTIST..."
  set +e
  python3 "$LYRICS_FETCHER" --artist "$ARTIST" --title "$TITLE" --out "$LYRICS_OUT" 2>/dev/null
  FLAG_RC=$?
  if [ $FLAG_RC -ne 0 ] || [ ! -s "$LYRICS_OUT" ]; then
    python3 "$LYRICS_FETCHER" "$ARTIST" "$TITLE" -o "$LYRICS_OUT"
    POS_RC=$?
    if [ $POS_RC -ne 0 ] || [ ! -s "$LYRICS_OUT" ]; then
      echo "[ERROR] Could not fetch lyrics."
      exit 1
    fi
  fi
  set -e
  echo "[OK] Lyrics saved to $LYRICS_OUT"
fi

# 2) audio
if [ $FORCE_AUDIO -eq 0 ] && [ -s "$AUDIO_OUT" ]; then
  echo "[SKIP] Audio already exists at $AUDIO_OUT"
else
  echo ">>> Downloading audio from YouTube..."
  SEARCH_QUERY="${ARTIST} ${TITLE}"
  python3 "$YT_PICKER" "$SEARCH_QUERY"
  NEWEST_MP3=$(ls -1t "$SONGS_DIR"/*.mp3 2>/dev/null | head -n 1 || true)
  if [ -z "$NEWEST_MP3" ]; then
    echo "[ERROR] YouTube download did not produce an mp3 in $SONGS_DIR"
    exit 1
  fi
  if [ "$NEWEST_MP3" != "$AUDIO_OUT" ]; then
    cp -f "$NEWEST_MP3" "$AUDIO_OUT"
  fi
  echo "[OK] Audio normalized to $AUDIO_OUT"
fi

# 3) align
if [ $FORCE_ALIGN -eq 0 ] && [ -s "$CSV_OUT" ]; then
  echo "[SKIP] CSV already exists at $CSV_OUT"
else
  echo ">>> Aligning lyrics to audio..."
  python3 "$ALIGNER" \
    --audio "$AUDIO_OUT" \
    --lyrics "$LYRICS_OUT" \
    --out "$CSV_OUT" \
    --model large-v3
  echo "[OK] CSV saved to $CSV_OUT"
fi

# 4) render — ALWAYS
echo ">>> Rendering karaoke video..."
python3 "$RENDERER" \
  --csv "$CSV_OUT" \
  --audio "$AUDIO_OUT" \
  --font-size "$FONT_SIZE" \
  --repo-root "$REPO_ROOT" \
  --offset-video -1.0 \
  "${EXTRA_RENDER_ARGS[@]}"

echo "[DONE] Karaoke video for $ARTIST – \"$TITLE\" is in ./output/"
# end of new_song_with_automated_timings.sh
