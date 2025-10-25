#!/bin/bash
# run_faster_whisper_cpu.sh
# Full non-VAD transcription with timestamps for entire track

set -e

AUDIO="songs/Red_Hot_Chili_Peppers_Around_the_World.mp3"
OUT_JSON="lyrics/Red_Hot_Chili_Peppers_Around_the_World_full.json"

python3 - <<'PYCODE'
from faster_whisper import WhisperModel
import json, os

audio = "songs/Red_Hot_Chili_Peppers_Around_the_World.mp3"
out_json = "lyrics/Red_Hot_Chili_Peppers_Around_the_World_full.json"

print("🔧 Loading Faster-Whisper (base.en, CPU float32)...")
model = WhisperModel("base.en", device="cpu", compute_type="float32")

print("🎙️ Transcribing full audio (no VAD)...")
segments, info = model.transcribe(audio, language="en", beam_size=1)

segs = []
for s in segments:
    segs.append({
        "start": round(s.start, 3),
        "end": round(s.end, 3),
        "text": s.text.strip()
    })

os.makedirs(os.path.dirname(out_json), exist_ok=True)
with open(out_json, "w", encoding="utf-8") as f:
    json.dump({"segments": segs}, f, indent=2, ensure_ascii=False)

print(f"✅ Saved full raw transcription → {out_json}")
print(f"Total segments: {len(segs)}   Duration: {info.duration:.1f}s")
PYCODE
