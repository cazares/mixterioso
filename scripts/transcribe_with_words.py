#!/usr/bin/env python3
from faster_whisper import WhisperModel
import json, sys
from pathlib import Path

if len(sys.argv) < 3:
    print("usage: transcribe_with_words.py input.mp3 output.json")
    sys.exit(1)

audio, out_json = Path(sys.argv[1]), Path(sys.argv[2])
model = WhisperModel("base", device="cpu", compute_type="float32")
segments, info = model.transcribe(str(audio), language="en", word_timestamps=True)
json.dump({"segments":[s._asdict() for s in segments]}, open(out_json,"w"), indent=2)
print(f"✅ saved {out_json}")
