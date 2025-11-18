rm wavs/nirvana*.wav
rm output/nirvana*.mp4
rm output/nirvana*.ass
rm timings/nirvana*.csv
rm mixes/nirvana*.wav

# python3 scripts/3_auto_timing.py --slug nirvana_come_as_you_are --debug

python3 scripts/3_auto_timing.py \
    --slug nirvana_come_as_you_are \
    --language en \
    --model-size distil-large-v3 \
    --beam-size 5 \
    --min-similarity 0.5 \
    --debug

python3 scripts/4_mp4.py \
  --slug nirvana_come_as_you_are \
  --profile karaoke \
  --offset -0.5

open output