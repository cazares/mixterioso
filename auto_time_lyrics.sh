# 0) (once) you already did this, but for completeness:
python3 -m venv lyrics-align-env
source lyrics-align-env/bin/activate
pip3 install stable-ts openai-whisper thefuzz numpy tqdm

# If you’re on Python 3.13 and ever use ForceAlign/Aeneas later, also do:
# pip3 install audioop-lts

# 1) Save the script below as scripts/lyrics_to_csv.py
# 2) Run it (it will pre-convert to 16k mono WAV to avoid pipe noise):
python3 scripts/lyrics_to_csv.py \
  --audio "songs/scar_tissue.mp3" \
  --lyrics "lyrics/scar_tissue.txt" \
  --out "lyrics/scar_tissue_aligned.csv" \
  --model large-v3 \
  --format line_start   # <- use this if your pipeline wants just line,start
