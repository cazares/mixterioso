#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./align.sh mp3s/song.mp3 txts/song.txt
#
# Output:
#   - converted WAV in wavs/
#   - LAB created automatically
#   - corpus/ populated
#   - aligned/TextGrid created by MFA

if [ $# -ne 2 ]; then
    echo "Usage: $0 <mp3_file> <txt_file>"
    exit 1
fi

MP3="$1"
TXT="$2"

# Extract basename without extension
BASENAME=$(basename "$MP3")
STEM="${BASENAME%.*}"

# Repo root is assumed to be /workspaces/mixterioso
ROOT="/workspaces/mixterioso"

MP3_DIR="$ROOT/mp3s"
WAV_DIR="$ROOT/wavs"
TXT_DIR="$ROOT/txts"
CORPUS_DIR="$ROOT/corpus"
ALIGNED_DIR="$ROOT/aligned"

mkdir -p "$WAV_DIR" "$CORPUS_DIR" "$ALIGNED_DIR"

# 1. Convert MP3 → WAV (mono 16kHz)
WAV_OUT="$WAV_DIR/${STEM}.wav"
echo "Converting MP3 → WAV..."
ffmpeg -y -i "$MP3" -ar 16000 -ac 1 "$WAV_OUT"

# 2. Create matching .lab from TXT
LAB_OUT="$WAV_DIR/${STEM}.lab"
echo "Creating LAB file..."
cp "$TXT" "$LAB_OUT"

# 3. Build corpus (WAV + LAB)
echo "Populating corpus/..."
cp "$WAV_OUT" "$CORPUS_DIR/"
cp "$LAB_OUT" "$CORPUS_DIR/"

# 4. Prepare JSON for FastAPI POST
REQ_JSON="$(mktemp)"
cat <<EOF > "$REQ_JSON"
{
  "corpus_dir": "$CORPUS_DIR",
  "dictionary_path": "/home/codespace/.local/share/montreal-forced-aligner/dictionary/english_us_arpa.dict",
  "acoustic_model_path": "/home/codespace/.local/share/montreal-forced-aligner/acoustic/english_us_arpa.zip",
  "output_dir": "$ALIGNED_DIR",
  "extra_args": ["--clean"]
}
EOF

# 5. Hit FastAPI align endpoint
echo "Sending alignment request..."
curl -s -X POST \
  http://localhost:8000/align \
  -H "Content-Type: application/json" \
  -d @"$REQ_JSON" > "$ROOT/align_result.json"

echo "Alignment result saved to align_result.json"
echo "Check $ALIGNED_DIR for the TextGrid output."
     
# Cleanup temp
rm "$REQ_JSON"

echo "Done."
