#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_whisper_real.py — guaranteed Faster-Whisper transcription with word timings.
"""

import json
from faster_whisper import WhisperModel
from pathlib import Path

audio_path = Path("songs/Red_Hot_Chili_Peppers_Around_the_World.mp3")
out_path = Path("lyrics/Red_Hot_Chili_Peppers_Around_the_World_real_words.json")

print("🎧 Loading Faster-Whisper model…")
model = WhisperModel("base", device="cpu", compute_type="float32")

print(f"🕒 Transcribing {audio_path.name} with word timestamps…")
segments, info = model.transcribe(
    str(audio_path),
    language="en",
    beam_size=1,
    best_of=1,
    vad_filter=False,
    word_timestamps=True,
)

out_data = {"language": info.language, "duration": info.duration, "segments": []}
for s in segments:
    seg_dict = {
        "start": s.start,
        "end": s.end,
        "text": s.text.strip(),
        "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])],
    }
    out_data["segments"].append(seg_dict)

out_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
print(f"✅ Saved → {out_path}")
print(f"  Segments: {len(out_data['segments'])}")
