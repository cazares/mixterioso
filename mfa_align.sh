#!/usr/bin/env bash
set -euo pipefail

ROOT="/workspaces/mixterioso"

CORPUS_DIR="${CORPUS_DIR:-$ROOT/corpus}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/aligned}"

# USE THE VENV'S MFA
MFA_BIN="$ROOT/mfa_env/bin/mfa"

DICT_PATH="${DICT_PATH:-$HOME/.local/share/montreal-forced-aligner/dictionary/english_us_arpa.dict}"
ACOUSTIC_MODEL_PATH="${ACOUSTIC_MODEL_PATH:-$HOME/.local/share/montreal-forced-aligner/acoustic/english_us_arpa.zip}"

mkdir -p "$OUTPUT_DIR"

echo "Running MFA align..."
echo "  corpus_dir          = $CORPUS_DIR"
echo "  dictionary_path     = $DICT_PATH"
echo "  acoustic_model_path = $ACOUSTIC_MODEL_PATH"
echo "  output_dir          = $OUTPUT_DIR"
echo "  mfa_bin             = $MFA_BIN"

"$MFA_BIN" align \
  "$CORPUS_DIR" \
  "$DICT_PATH" \
  "$ACOUSTIC_MODEL_PATH" \
  "$OUTPUT_DIR" \
  --clean

echo "Done. Check TextGrid files in: $OUTPUT_DIR"
