#!/usr/bin/env bash
set -euo pipefail

# new_song_with_automated_timings.sh
# Usage:
#   ./new_song_with_automated_timings.sh "Red Hot Chili Peppers" "Californication"
#   ./new_song_with_automated_timings.sh "Red Hot Chili Peppers" "Californication" --extra-delay 1.0

if [ $# -lt 2 ]; then
  echo "usage: $0 \"Artist\" \"Title\" [render extra args...]"
  exit 1
fi

ARTIST="$1"
TITLE="$2"
shift 2 || true   # extra args → render_from_csv.py

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

# ---------------------------------------------------------------------
# locate python helpers
# ---------------------------------------------------------------------
if [ -f "$SCRIPTS_DIR/lyrics_fetcher.py" ]; then
  LYRICS_FETCHER="$SCRIPTS_DIR/lyrics_fetcher.py"
elif [ -f "$REPO_ROOT/lyrics_fetcher.py" ]; then
  LYRICS_FETCHER="$REPO_ROOT/lyrics_fetcher.py"
else
  echo "[ERROR] lyrics_fetcher.py not found in scripts/ or repo root."
  exit 1
fi

if [ -f "$SCRIPTS_DIR/youtube_audio_picker.py" ]; then
  YT_PICKER="$SCRIPTS_DIR/youtube_audio_picker.py"
elif [ -f "$REPO_ROOT/youtube_audio_picker.py" ]; then
  YT_PICKER="$REPO_ROOT/youtube_audio_picker.py"
else
  echo "[ERROR] youtube_audio_picker.py not found in scripts/ or repo root."
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

# ---------------------------------------------------------------------
# 1) fetch lyrics (try flag, then positional)
# ---------------------------------------------------------------------
echo ">>> Fetching lyrics for \"$TITLE\" by $ARTIST..."
FETCH_OK=0
set +e
python3 "$LYRICS_FETCHER" --artist "$ARTIST" --title "$TITLE" --out "$LYRICS_OUT"
if [ $? -ne 0 ]; then
  python3 "$LYRICS_FETCHER" "$ARTIST" "$TITLE" -o "$LYRICS_OUT"
  FETCH_OK=$?
else
  FETCH_OK=0
fi
set -e

if [ $FETCH_OK -ne 0 ] || [ ! -s "$LYRICS_OUT" ]; then
  echo "[ERROR] Could not fetch lyrics for \"$TITLE\" by $ARTIST."
  exit 1
fi
echo "[OK] Lyrics saved to $LYRICS_OUT"

# ---------------------------------------------------------------------
# 2) download audio (try flag mode, then positional search)
# ---------------------------------------------------------------------
echo ">>> Downloading audio from YouTube..."

# try 1: flag-style (maybe your picker supports it)
YT_ERR_LOG=$(mktemp)
set +e
python3 "$YT_PICKER" \
  --artist "$ARTIST" \
  --title "$TITLE" \
  --out "$AUDIO_OUT" 2> "$YT_ERR_LOG"
YT_RC=$?
set -e

NEED_FALLBACK=0
if [ $YT_RC -ne 0 ]; then
  NEED_FALLBACK=1
elif ! [ -f "$AUDIO_OUT" ]; then
  NEED_FALLBACK=1
else
  # some versions print "No results found" but still exit 0
  if grep -qi "No results found" "$YT_ERR_LOG"; then
    NEED_FALLBACK=1
  fi
fi

if [ $NEED_FALLBACK -eq 1 ]; then
  echo "[WARN] flag-style YouTube download failed or no results. Retrying with single search query..."
  SEARCH_QUERY="${ARTIST} ${TITLE}"
  python3 "$YT_PICKER" "$SEARCH_QUERY" "$AUDIO_OUT"
fi

if [ ! -f "$AUDIO_OUT" ]; then
  echo "[ERROR] Failed to download audio for \"$TITLE\". Aborting."
  exit 1
fi
echo "[OK] Audio saved to $AUDIO_OUT"

# ---------------------------------------------------------------------
# 3) align lyrics -> CSV
# ---------------------------------------------------------------------
echo ">>> Aligning lyrics to audio..."
python3 "$ALIGNER" \
  --audio "$AUDIO_OUT" \
  --lyrics "$LYRICS_OUT" \
  --out "$CSV_OUT" \
  --model large-v3
echo "[OK] CSV with timings saved to $CSV_OUT"

# ---------------------------------------------------------------------
# 4) render (baked-in good settings)
# ---------------------------------------------------------------------
echo ">>> Rendering karaoke video..."
python3 "$RENDERER" \
  --csv "$CSV_OUT" \
  --audio "$AUDIO_OUT" \
  --font-size 50 \
  --repo-root "$REPO_ROOT" \
  --offset-video -1.0 \
  "$@"

echo "[DONE] Karaoke video for $ARTIST – \"$TITLE\" is in ./output/"
# end of new_song_with_automated_timings.sh
