#!/usr/bin/env bash
# gen_video.sh — pipeline: lyrics → audio → align → demucs → render (multi-variant)
# now with:
#  - intro screen
#  - gap filler symbols
#  - gap-threshold + gap-delay pass-through
#  - REAL per-vocal-pct audio from demucs stems (when available)
#  - ✅ REUSE EXISTING STEMS
#  - ✅ PRE-NUKE output/<name>.mp4 if it's a dir/file
#  - ✅ optional --car-font-size passthrough
#  - ✅ accent-friendly slugify
#  - ✅ YouTube: PASS POSITIONAL QUERY (no --artist/--title)

set -euo pipefail

# --- colors ---------------------------------------------------------------
if [ -t 1 ]; then
  RED=$'\033[0;31m'
  GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'
  CYAN=$'\033[0;36m'
  MAGENTA=$'\033[0;35m'
  BOLD=$'\033[1m'
  RESET=$'\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; MAGENTA=''; BOLD=''; RESET=''
fi
info()  { printf "%s%s%s\n" "$CYAN" "$*" "$RESET"; }
ok()    { printf "%s%s%s\n" "$GREEN" "$*" "$RESET"; }
warn()  { printf "%s%s%s\n" "$YELLOW" "$*" "$RESET"; }
err()   { printf "%s%s%s\n" "$RED" "$*" "$RESET"; }

# --- paths ----------------------------------------------------------------
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$ROOT/scripts"
LYRICS_DIR="$ROOT/auto_lyrics"
SONGS_DIR="$ROOT/songs"
OUTPUT_DIR="$ROOT/output"
STEMS_ROOT="$OUTPUT_DIR/stems"

mkdir -p "$LYRICS_DIR" "$SONGS_DIR" "$OUTPUT_DIR" "$STEMS_ROOT"

# --- helpers --------------------------------------------------------------
slugify() {
  local s="$1"
  s=$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]')
  s=$(printf '%s' "$s" | tr 'áéíóúüñÁÉÍÓÚÜÑ' 'aeiouunaeiouun')
  s=$(printf '%s' "$s" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
  printf '%s\n' "$s"
}

# 👇 deaccent but KEEP SPACES (for YouTube queries)
deaccent_keep_spaces() {
  local s="$1"
  s=$(printf '%s' "$s" | tr 'áéíóúüñÁÉÍÓÚÜÑ' 'aeiouunaeiouun')
  printf '%s\n' "$s"
}

is_pct() {
  [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 0 ] && [ "$1" -le 100 ]
}

find_demucs_bin() {
  if command -v demucs >/dev/null 2>&1; then
    echo "demucs"; return
  fi
  if [ -x "$ROOT/demucs_env/bin/demucs" ]; then
    echo "$ROOT/demucs_env/bin/demucs"; return
  fi
  if python3 -m demucs --help >/dev/null 2>&1; then
    echo "python3 -m demucs"; return
  fi
  echo ""
}

# ✅ NEW: detect existing stems for this song and reuse
find_existing_stems_dir() {
  local stems_export_dir="$1"
  local audio_base="$2"

  if [ -d "$stems_export_dir/htdemucs_6s/$audio_base" ]; then
    echo "$stems_export_dir/htdemucs_6s/$audio_base"; return
  fi
  if [ -d "$stems_export_dir/htdemucs/$audio_base" ]; then
    echo "$stems_export_dir/htdemucs/$audio_base"; return
  fi
  # last-resort: 2-stem often ends here too
  if [ -d "$stems_export_dir/htdemucs_2s/$audio_base" ]; then
    echo "$stems_export_dir/htdemucs_2s/$audio_base"; return
  fi
  echo ""
}

# --- args -----------------------------------------------------------------
if [ $# -lt 2 ]; then
  err "Usage: $0 \"Artist\" \"Title\" [--font-size N] [--car-font-size N] [--max-chars N] [--offset-video SEC] [--extra-delay SEC] [--hpad-pct N] [--valign ...] [--vocal-pcts \"0 20 100\"] [--gap-threshold 5.0] [--gap-delay 2.0]"
  exit 1
fi

ARTIST="$1"; shift
TITLE="$1"; shift

FONT_SIZE=140
CAR_FONT_SIZE=""
MAX_CHARS=18
OFFSET_VIDEO=-1.0
EXTRA_DELAY=0.0
HPAD_PCT=6
VALIGN=middle
GAP_THRESHOLD=5.0
GAP_DELAY=2.0

HAS_VOCAL_PCTS=0
VOCAL_PCTS_STR=""

USER_SELECTED_STEMS=0
SEL_VOCALS=0;  VOCALS_LEVEL=100
SEL_DRUMS=0;   DRUMS_LEVEL=100
SEL_BASS=0;    BASS_LEVEL=100
SEL_GUITAR=0;  GUITAR_LEVEL=100

while [ $# -gt 0 ]; do
  case "$1" in
    --font-size)     FONT_SIZE="$2"; shift 2;;
    --car-font-size) CAR_FONT_SIZE="$2"; shift 2;;
    --max-chars)     MAX_CHARS="$2"; shift 2;;
    --offset-video)  OFFSET_VIDEO="$2"; shift 2;;
    --extra-delay)   EXTRA_DELAY="$2"; shift 2;;
    --hpad-pct)      HPAD_PCT="$2"; shift 2;;
    --valign)        VALIGN="$2"; shift 2;;
    --gap-threshold) GAP_THRESHOLD="$2"; shift 2;;
    --gap-delay)     GAP_DELAY="$2"; shift 2;;
    --vocal-pcts)
      HAS_VOCAL_PCTS=1
      VOCAL_PCTS_STR="$2"
      shift 2;;
    --vocals)
      USER_SELECTED_STEMS=1; SEL_VOCALS=1
      if [ $# -gt 1 ] && is_pct "$2"; then VOCALS_LEVEL="$2"; shift 2; else shift 1; fi;;
    --drums)
      USER_SELECTED_STEMS=1; SEL_DRUMS=1
      if [ $# -gt 1 ] && is_pct "$2"; then DRUMS_LEVEL="$2"; shift 2; else shift 1; fi;;
    --bass)
      USER_SELECTED_STEMS=1; SEL_BASS=1
      if [ $# -gt 1 ] && is_pct "$2"; then BASS_LEVEL="$2"; shift 2; else shift 1; fi;;
    --guitar)
      USER_SELECTED_STEMS=1; SEL_GUITAR=1
      if [ $# -gt 1 ] && is_pct "$2"; then GUITAR_LEVEL="$2"; shift 2; else shift 1; fi;;
    *)
      warn "Unknown arg: $1 (ignored)"
      shift 1;;
  esac
done

if [ $HAS_VOCAL_PCTS -eq 1 ] && [ $USER_SELECTED_STEMS -eq 1 ]; then
  err "You can't mix --vocal-pcts with --vocals/--bass/--drums/--guitar. Pick one."
  exit 1
fi

ARTIST_SLUG="$(slugify "$ARTIST")"
TITLE_SLUG="$(slugify "$TITLE")"

LYRICS_PATH="$LYRICS_DIR/${ARTIST_SLUG}-${TITLE_SLUG}.txt"
CSV_PATH="$LYRICS_DIR/${ARTIST_SLUG}-${TITLE_SLUG}.csv"
AUDIO_PATH="$SONGS_DIR/auto_${ARTIST_SLUG}-${TITLE_SLUG}.mp3"
AUDIO_MONO_PATH="$SONGS_DIR/auto_${ARTIST_SLUG}-${TITLE_SLUG}_mono.mp3"

STEMS_EXPORT_DIR="$STEMS_ROOT/${ARTIST_SLUG}-${TITLE_SLUG}"
mkdir -p "$STEMS_EXPORT_DIR"

info ">>> Preparing karaoke for: ${BOLD}${ARTIST} – \"${TITLE}\"${RESET}"

# 1) lyrics ---------------------------------------------------------------
if [ -f "$LYRICS_PATH" ]; then
  info "[INFO] Lyrics already exist at $LYRICS_PATH — skipping fetch."
else
  if [ -f "$SCRIPTS_DIR/lyrics_fetcher.py" ]; then
    info ">>> Fetching lyrics for \"${TITLE}\" by ${ARTIST}..."
    python3 "$SCRIPTS_DIR/lyrics_fetcher.py" "$ARTIST" "$TITLE" -o "$LYRICS_PATH"
    ok "[OK] Lyrics saved to $LYRICS_PATH"
  else
    err "[ERROR] scripts/lyrics_fetcher.py not found."
    exit 1
  fi
fi

# 2) audio from YouTube ----------------------------------------------------
if [ -f "$AUDIO_PATH" ]; then
  info "[INFO] Audio already exists at $AUDIO_PATH — skipping YouTube download."
else
  if [ -f "$SCRIPTS_DIR/youtube_audio_picker.py" ]; then
    info ">>> Downloading audio from YouTube (yt-dlp)..."

    # 1) try with original accents
    if python3 "$SCRIPTS_DIR/youtube_audio_picker.py" \
        --artist "$ARTIST" \
        --title "$TITLE" \
        --out "$AUDIO_PATH"; then
      ok "[OK] Audio saved to $AUDIO_PATH"
    else
      warn "[WARN] YouTube search with accents failed, retrying without accents…"

      # deaccent artist/title for Spanish-ish names
      PLAIN_ARTIST=$(printf '%s' "$ARTIST" | tr 'áéíóúüñÁÉÍÓÚÜÑ' 'aeiouunaeiouun')
      PLAIN_TITLE=$(printf '%s' "$TITLE"   | tr 'áéíóúüñÁÉÍÓÚÜÑ' 'aeiouunaeiouun')

      # 2) try again with deaccented flags
      if python3 "$SCRIPTS_DIR/youtube_audio_picker.py" \
          --artist "$PLAIN_ARTIST" \
          --title "$PLAIN_TITLE" \
          --out "$AUDIO_PATH"; then
        ok "[OK] Audio saved to $AUDIO_PATH"
      else
        warn "[WARN] Deaccented artist/title still failed — trying plain query…"

        # 3) final fallback: plain query, --out is a SEPARATE arg (the bug you hit)
        if python3 "$SCRIPTS_DIR/youtube_audio_picker.py" \
            --query "$PLAIN_ARTIST $PLAIN_TITLE" \
            --out "$AUDIO_PATH"; then
          ok "[OK] Audio saved to $AUDIO_PATH"
        else
          err "[ERROR] Could not download audio from YouTube for: $PLAIN_ARTIST $PLAIN_TITLE"
          err "      Try: python3 scripts/youtube_audio_picker.py --query \"$PLAIN_ARTIST $PLAIN_TITLE\" --out \"$AUDIO_PATH\""
          exit 1
        fi
      fi
    fi
  else
    err "[ERROR] scripts/youtube_audio_picker.py not found."
    exit 1
  fi
fi

# 2b) TRUE MONO ------------------------------------------------------------
if [ -f "$AUDIO_MONO_PATH" ]; then
  info "[INFO] Mono audio already exists at $AUDIO_MONO_PATH — reusing."
else
  info ">>> Converting to TRUE MONO (L+R avg) @ 48kHz..."
  ffmpeg -y -i "$AUDIO_PATH" -ac 1 -ar 48000 -b:a 192k "$AUDIO_MONO_PATH" >/dev/null 2>&1
  ok "[OK] Mono audio at $AUDIO_MONO_PATH"
fi

# 3) align -----------------------------------------------------------------
if [ -f "$CSV_PATH" ]; then
  info "[INFO] CSV already exists at $CSV_PATH — skipping alignment."
else
  if [ -f "$SCRIPTS_DIR/align_to_csv.py" ]; then
    info ">>> Aligning lyrics to audio (large-v3)..."
    python3 "$SCRIPTS_DIR/align_to_csv.py" \
      --audio "$AUDIO_MONO_PATH" \
      --lyrics "$LYRICS_PATH" \
      --out "$CSV_PATH" \
      --model large-v3
    ok "[OK] CSV saved to $CSV_PATH"
  else
    err "[ERROR] scripts/align_to_csv.py not found."
    exit 1
  fi
fi

# 4) DEMUCS with REUSE -----------------------------------------------------
DEMUCS_BIN="$(find_demucs_bin)"
BEST_STEMS_DIR=""

AUDIO_BASENAME="$(basename "$AUDIO_MONO_PATH")"
AUDIO_BASE_NOEXT="${AUDIO_BASENAME%.*}"

if [ -n "$DEMUCS_BIN" ]; then
  EXISTING_DIR="$(find_existing_stems_dir "$STEMS_EXPORT_DIR" "$AUDIO_BASE_NOEXT")"
  if [ -n "$EXISTING_DIR" ]; then
    ok "[REUSE] Found existing Demucs stems at $EXISTING_DIR — skipping separation."
    BEST_STEMS_DIR="$EXISTING_DIR"
  else
    info ">>> [DEMUCS] Running separation (6 → 4 → 2) …"
    DEMUCS_BASE_OUT="$STEMS_EXPORT_DIR"

    if $DEMUCS_BIN -n htdemucs_6s -o "$DEMUCS_BASE_OUT" "$AUDIO_MONO_PATH" 2>&1 | tee "$DEMUCS_BASE_OUT/demucs_6s.log"; then
      BEST_STEMS_DIR="$DEMUCS_BASE_OUT/htdemucs_6s/$AUDIO_BASE_NOEXT"
      ok "[OK] Demucs 6-stem succeeded → $BEST_STEMS_DIR"
    else
      warn "[WARN] 6-stem failed, trying 4-stem (htdemucs)…"
      if $DEMUCS_BIN -n htdemucs -o "$DEMUCS_BASE_OUT" "$AUDIO_MONO_PATH" 2>&1 | tee "$DEMUCS_BASE_OUT/demucs_4s.log"; then
        BEST_STEMS_DIR="$DEMUCS_BASE_OUT/htdemucs/$AUDIO_BASE_NOEXT"
        ok "[OK] Demucs 4-stem succeeded → $BEST_STEMS_DIR"
      else
        warn "[WARN] 4-stem failed, trying 2-stem (vocals)…"
        if $DEMUCS_BIN --two-stems=vocals -o "$DEMUCS_BASE_OUT" "$AUDIO_MONO_PATH" 2>&1 | tee "$DEMUCS_BASE_OUT/demucs_2s.log"; then
          BEST_STEMS_DIR="$DEMUCS_BASE_OUT/htdemucs/$AUDIO_BASE_NOEXT"
          ok "[OK] Demucs 2-stem succeeded → $BEST_STEMS_DIR"
        else
          err "[ERROR] All demucs attempts failed — will use mono for ALL variants."
          BEST_STEMS_DIR=""
        fi
      fi
    fi
  fi
else
  warn "[WARN] demucs not found — ALL variants will sound the same (mono)."
fi

# 5) decide what to render -------------------------------------------------
RENDER_PCTS=()
if [ $HAS_VOCAL_PCTS -eq 1 ]; then
  # shellcheck disable=SC2206
  RENDER_PCTS=($VOCAL_PCTS_STR)
else
  RENDER_PCTS=("100")
fi

info ">>> Rendering karaoke video(s): ${RENDER_PCTS[*]}"

# 6) per-pct: build audio --------------------------------------------------
MIXED_AUDIO_DIR="$SONGS_DIR/mixed"
mkdir -p "$MIXED_AUDIO_DIR"

for pct in "${RENDER_PCTS[@]}"; do
  OUT_NAME="${ARTIST_SLUG}-${TITLE_SLUG}_v${pct}"
  AUDIO_FOR_THIS="$AUDIO_MONO_PATH"

  if [ -n "${BEST_STEMS_DIR:-}" ] && [ -d "$BEST_STEMS_DIR" ]; then
    inputs=()
    filters=()
    idx=0

    vocal_gain="$(python3 - <<EOF
p = float("$pct")
print(p/100.0)
EOF
)"

    for stem in vocals.wav vocal.wav; do
      if [ -f "$BEST_STEMS_DIR/$stem" ]; then
        inputs+=("-i" "$BEST_STEMS_DIR/$stem")
        filters+=("[${idx}:a]volume=${vocal_gain}[a${idx}]")
        idx=$((idx+1))
        break
      fi
    done

    for stem in drums.wav bass.wav other.wav guitar.wav piano.wav keys.wav; do
      if [ -f "$BEST_STEMS_DIR/$stem" ]; then
        inputs+=("-i" "$BEST_STEMS_DIR/$stem")
        filters+=("[${idx}:a]volume=1.0[a${idx}]")
        idx=$((idx+1))
      fi
    done

    if [ ${#inputs[@]} -gt 0 ]; then
      fc=""
      for f in "${filters[@]}"; do fc+="$f;"; done

      outs=""
      for ((j=0; j<idx; j++)); do outs+="[a${j}]"; done

      fc+="${outs}amix=inputs=${idx}:normalize=0[outa]"

      MIXED_PATH="$MIXED_AUDIO_DIR/${TITLE_SLUG}_v${pct}.wav"
      info "[MIX] building mix for ${pct}% → $MIXED_PATH"

      if ffmpeg -y "${inputs[@]}" -filter_complex "$fc" -map "[outa]" -ar 48000 -ac 1 -b:a 192k "$MIXED_PATH" >/dev/null 2>&1; then
        AUDIO_FOR_THIS="$MIXED_PATH"
      else
        warn "[WARN] mix for ${pct}% failed, falling back to mono."
        AUDIO_FOR_THIS="$AUDIO_MONO_PATH"
      fi
    else
      warn "[WARN] demucs produced no usable stems — falling back to mono."
      AUDIO_FOR_THIS="$AUDIO_MONO_PATH"
    fi
  else
    warn "[WARN] No demucs stems — ${pct}% will sound same as others."
  fi

  FINAL_MP4="$OUTPUT_DIR/${OUT_NAME}.mp4"
  if [ -d "$FINAL_MP4" ]; then
    warn "[CLEANUP] $FINAL_MP4 was a directory — removing it so we can write the mp4."
    rm -rf "$FINAL_MP4"
  elif [ -f "$FINAL_MP4" ]; then
    warn "[CLEANUP] $FINAL_MP4 already existed — removing old file."
    rm -f "$FINAL_MP4"
  fi

  PY_ARGS=(
    "$SCRIPTS_DIR/render_from_csv.py"
    --csv "$CSV_PATH"
    --audio "$AUDIO_FOR_THIS"
    --font-size "$FONT_SIZE"
    --repo-root "$ROOT"
    --offset-video "$OFFSET_VIDEO"
    --extra-delay "$EXTRA_DELAY"
    --hpad-pct "$HPAD_PCT"
    --valign "$VALIGN"
    --output-name "$OUT_NAME"
    --max-chars "$MAX_CHARS"
    --artist "$ARTIST"
    --title "$TITLE"
    --gap-threshold "$GAP_THRESHOLD"
    --gap-delay "$GAP_DELAY"
    --no-open
  )
  if [ -n "$CAR_FONT_SIZE" ]; then
    PY_ARGS+=( --car-font-size "$CAR_FONT_SIZE" )
  fi

  python3 "${PY_ARGS[@]}"
done

ok "[DONE] Karaoke video(s) for ${ARTIST} – \"${TITLE}\" are in $OUTPUT_DIR/"
if command -v open >/dev/null 2>&1; then
  open "$OUTPUT_DIR" >/dev/null 2>&1 || true
fi
# end of gen_video.sh
