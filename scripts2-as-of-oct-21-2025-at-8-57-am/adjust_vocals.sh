#!/bin/bash
# adjust_vocals.sh — rebuild mix with vocals at given % volume

if [ -z "$1" ]; then
  echo "Usage: $0 <vocal_percent>  (e.g. 20 for 20%)"
  exit 1
fi

PCT="$1"
VOL=$(echo "$PCT / 100" | bc -l)

ffmpeg -y \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/vocals.wav" \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/bass.wav" \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/drums.wav" \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/guitar.wav" \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/piano.wav" \
  -i "separated/htdemucs_6s/El_Caballo_de_mi_Padre/other.wav" \
  -filter_complex "[0]volume=${VOL}[a0];[a0][1][2][3][4][5]amix=inputs=6:normalize=0" \
  -c:a libmp3lame -qscale:a 2 \
  "El_Caballo_de_mi_Padre_vocals${PCT}pct.mp3"

