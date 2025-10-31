#!/usr/bin/env bash
# whisper_cli_pipeline.sh
set -euo pipefail
set -x

# --- Config ---
TITLE="Me Dice Que Me Ama"
ARTIST="Jesus Adrian Romero"
MP3="songs/auto_jesus-adrian-romero-me-dice-que-me-ama.mp3"
BASE="auto_jesus-adrian-romero-me-dice-que-me-ama"
ENABLE_DEMUCS="${ENABLE_DEMUCS:-0}"   # set to 1 to also run vocals-only pass

# --- Preconditions ---
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1"; exit 1; }; }
need curl
need jq
need whisper
if [[ "$ENABLE_DEMUCS" == "1" ]]; then need demucs; fi

mkdir -p lyrics whisper_out whisper_out_vocals

# --- 1) Fetch lyrics (plain) via LRCLIB ---
curl "https://lrclib.net/api/search?track_name=$(python3 - <<'PY'
import urllib.parse; print(urllib.parse.quote("Me Dice Que Me Ama"))
PY
)&artist_name=$(python3 - <<'PY'
import urllib.parse; print(urllib.parse.quote("Jesus Adrian Romero"))
PY
)" \
| jq -r '((map(select((.trackName|ascii_downcase)=="me dice que me ama" and (.artistName|ascii_downcase)=="jesus adrian romero"))|first)//.[0])|.plainLyrics' \
| sed 's/\r$//' > "lyrics/${BASE}.txt"

# --- 2) Fetch lyrics (synced LRC) via LRCLIB ---
curl "https://lrclib.net/api/search?track_name=$(python3 - <<'PY'
import urllib.parse; print(urllib.parse.quote("Me Dice Que Me Ama"))
PY
)&artist_name=$(python3 - <<'PY'
import urllib.parse; print(urllib.parse.quote("Jesus Adrian Romero"))
PY
)" \
| jq -r '((map(select((.trackName|ascii_downcase)=="me dice que me ama" and (.artistName|ascii_downcase)=="jesus adrian romero"))|first)//.[0])|.syncedLyrics' \
| sed 's/\r$//' > "lyrics/${BASE}.lrc"

# --- 3) Whisper on full mix (word timestamps, JSON out) ---
whisper "$MP3" \
  --model large-v3 \
  --language es \
  --task transcribe \
  --word_timestamps True \
  --condition_on_previous_text False \
  --temperature 0 \
  --device cpu \
  --fp16 False \
  --output_format json \
  --output_dir "whisper_out"

# Quick TSV dump of words from full-mix run
jq -r '.segments[]?.words[]? | "\(.word)\t\(.start)\t\(.end)"' "whisper_out/${BASE}.json" \
| sed 's/^[[:space:]]\+//; s/[[:space:]]\+$//' > "whisper_out/${BASE}_words.tsv"

# --- 4) Optional: separate vocals and transcribe vocals only ---
if [[ "$ENABLE_DEMUCS" == "1" ]]; then
  demucs -n htdemucs_6s "$MP3"
  whisper "separated/htdemucs_6s/${BASE}/vocals.wav" \
    --model large-v3 \
    --language es \
    --task transcribe \
    --word_timestamps True \
    --condition_on_previous_text False \
    --temperature 0 \
    --device cpu \
    --fp16 False \
    --output_format json \
    --output_dir "whisper_out_vocals"

  jq -r '.segments[]?.words[]? | "\(.word)\t\(.start)\t\(.end)"' "whisper_out_vocals/${BASE}.json" \
  | sed 's/^[[:space:]]\+//; s/[[:space:]]\+$//' > "whisper_out_vocals/${BASE}_words.tsv"
fi

# --- 5) Optional: extract simple [time\ttext] from LRC for quick inspection ---
if [[ -s "lyrics/${BASE}.lrc" ]]; then
  awk -F']' '/^\[/{split($1,t,"["); gsub(",",".",t[2]); print t[2] "\t" $2}' "lyrics/${BASE}.lrc" \
  > "lyrics/${BASE}_lrc.tsv" || true
fi

echo "Done.
- Plain lyrics:    lyrics/${BASE}.txt
- Synced LRC:      lyrics/${BASE}.lrc
- Whisper JSON:    whisper_out/${BASE}.json
- Words TSV:       whisper_out/${BASE}_words.tsv
${ENABLE_DEMUCS:+- Vocals JSON:    whisper_out_vocals/${BASE}.json
- Vocals words:    whisper_out_vocals/${BASE}_words.tsv}
"
# end of whisper_cli_pipeline.sh
