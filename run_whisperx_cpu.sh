#!/bin/bash
# run_whisperx_cpu.sh
# Full WhisperX pipeline in CPU float32 mode
# Outputs a complete, pretty JSON alignment

set -e

# ---------------------- CONFIG ---------------------- #
export PYANNOTE_AUDIO_NO_PREPROCESSING=1
export CTRANSLATE2_FORCE_CPU=1

AUDIO="songs/Red_Hot_Chili_Peppers_Around_the_World.mp3"
OUT_JSON="lyrics/Red_Hot_Chili_Peppers_Around_the_World_full.json"

# ---------------------- RUN ---------------------- #
python3 - <<'PYCODE'
import whisperx, json, os

audio = "songs/Red_Hot_Chili_Peppers_Around_the_World.mp3"
out_json = "lyrics/Red_Hot_Chili_Peppers_Around_the_World_full.json"

print("🔧 Loading WhisperX (float32 CPU mode)...")
model = whisperx.load_model("small", device="cpu", compute_type="float32")

print("🎙️ Transcribing full audio (chunk_size=120s, no VAD)...")
result = model.transcribe(
    audio,
    language="en",
    vad_method=None,
    chunk_size=120
)

print("🔩 Aligning with WAV2VEC2_ASR_LARGE_LV60K_960H...")
align_model, metadata = whisperx.load_align_model(language_code="en", device="cpu")
aligned = whisperx.align(result["segments"], align_model, metadata, audio, device="cpu")

os.makedirs(os.path.dirname(out_json), exist_ok=True)
with open(out_json, "w", encoding="utf-8") as f:
    json.dump(aligned, f, indent=2, ensure_ascii=False)

print(f"✅ Saved full alignment → {out_json}")
print(f"Total segments: {len(aligned.get('segments', []))}")
PYCODE
