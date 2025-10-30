#!/usr/bin/env bash
set -euo pipefail

# Usage: ./new_song.sh <title_slug> [extra args...]
TITLE="${1:-}"
if [[ -z "$TITLE" ]]; then
  echo "Usage: $0 <title_slug> [extra args...]" >&2
  exit 1
fi
shift

python3 scripts/car_karaoke_time.py \
  --lyrics "lyrics/${TITLE}.txt" \
  --vocal-pcts 50 75 \
  --high-quality \
  --font-size 140 \
  --offset-video -1.0 \
  "$@"

# end of new_song.sh
