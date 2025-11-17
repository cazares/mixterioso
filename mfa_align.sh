#!/usr/bin/env bash
set -euo pipefail

# Minimal wrapper around `mfa align`.
# Usage:
#   ./mfa_align.sh
# or
#   CORPUS_DIR=/some/where ./mfa_align.sh
#
# Assumes:
# - mfa is installed
# - english_us_arpa models are installed by MFA
# - corpus_dir contains .wav + .lab pairs

ROOT="/workspaces/mixterioso"

CORPUS_DIR="${CORPUS_DIR:-$ROOT/corpus}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/aligned}"

DICT_PATH="${DICT_PATH:-$HOME/.local/share/montreal-forced-aligner/dictionary/english_us_arpa.dict}"
ACOUSTIC_MODEL_PATH="${ACOUSTIC_MODEL_PATH:-$HOME/.local/share/montreal-forced-aligner/acoustic/english_us_arpa.zip}"

mkdir -p "$OUTPUT_DIR"

echo "Running MFA align..."
echo "  corpus_dir          = $CORPUS_DIR"
echo "  dictionary_path     = $DICT_PATH"
echo "  acoustic_model_path = $ACOUSTIC_MODEL_PATH"
echo "  output_dir          = $OUTPUT_DIR"

mfa align \
  "$CORPUS_DIR" \
  "$DICT_PATH" \
  "$ACOUSTIC_MODEL_PATH" \
  "$OUTPUT_DIR" \
  --clean

echo "Done. Check TextGrid files in: $OUTPUT_DIR"
