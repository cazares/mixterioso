#!/usr/bin/env python3
from faster_whisper import WhisperModel
import json, sys
audio, out = sys.argv[1], sys.argv[2]

model = WhisperModel("base", device="cpu", compute_type="float32")
segments, info = model.transcribe(
    audio,
    language="en",
    beam_size=5,
    vad_filter=False,
    word_timestamps=True,
    chunk_length=0
)

result = {"segments": []}
for s in segments:
    seg_dict = {
        "start": float(s.start),
        "end": float(s.end),
        "text": s.text,
        "words": [{"word": w.word, "start": float(w.start), "end": float(w.end)} for w in s.words or []]
    }
    result["segments"].append(seg_dict)

with open(out, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(f"✅ Saved → {out} ({len(result['segments'])} segments)")
