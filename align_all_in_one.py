#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path
import whisperx
from faster_whisper import WhisperModel


# ------------------------------------------------------------
# Convert MP3 → WAV (analysis only)
# ------------------------------------------------------------
def convert_mp3_to_wav(mp3_path: Path, wav_path: Path):
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[FFMPEG] Converting {mp3_path} → {wav_path}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp3_path),
        "-ac", "1",
        "-ar", "16000",
        str(wav_path)
    ]
    subprocess.run(cmd, check=True)


# ------------------------------------------------------------
# Convert WhisperX alignment to simple CSV (line,start,text)
# ------------------------------------------------------------
def alignment_to_csv(aligned: dict, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[CSV] Writing {csv_path}")

    with open(csv_path, "w") as f:
        f.write("line_index,time_secs,text\n")

        idx = 0
        for seg in aligned["segments"]:
            text = seg.get("text", "").strip()
            start = seg.get("start")

            if not text:
                continue
            if start is None:
                continue

            f.write(f"{idx},{start:.3f},\"{text}\"\n")
            idx += 1


# ------------------------------------------------------------
# Main forced alignment pipeline
# ------------------------------------------------------------
def run_alignment(slug: str):
    root = Path(".").resolve()

    mp3_path = root / "mp3s" / f"{slug}.mp3"
    txt_path = root / "txts" / f"{slug}.txt"
    wav_path = root / "wavs" / f"{slug}.wav"
    csv_path = root / "timings" / f"{slug}.csv"

    # --- sanity checks ---
    if not mp3_path.exists():
        raise FileNotFoundError(f"Missing MP3: {mp3_path}")
    if not txt_path.exists():
        raise FileNotFoundError(f"Missing TXT: {txt_path}")

    # --- step 1: mp3→wav ---
    convert_mp3_to_wav(mp3_path, wav_path)

    # --- load text (lyrics) ---
    text = txt_path.read_text().strip()

    # --- step 2: ASR (WITHOUT VAD) ---
    print("[ASR] Loading Whisper (medium, int8)…")
    device = "cpu"
    asr_model = WhisperModel("medium", device=device, compute_type="int8")

    print("[ASR] Transcribing (bypassing VAD)…")
    segments, _ = asr_model.transcribe(str(wav_path))

    whisper_result = {"segments": []}
    for seg in segments:
        whisper_result["segments"].append({
            "text": seg.text,
            "start": seg.start,
            "end": seg.end
        })

    # audio waveform for forced alignment
    print("[WhisperX] Loading audio for alignment…")
    audio = whisperx.load_audio(str(wav_path))

    # --- step 3: load alignment model ---
    print("[WhisperX] Loading alignment model (Wav2Vec2)…")
    model_a, metadata = whisperx.load_align_model(
        language_code="en",
        device=device
    )

    # --- step 4: forced alignment ---
    print("[WhisperX] Performing forced alignment…")
    aligned = whisperx.align(
        whisper_result["segments"],
        model_a,
        metadata,
        audio,
        device,
        text=text
    )

    # --- step 5: save csv for 4_mp4 ---
    alignment_to_csv(aligned, csv_path)

    print("\n========================================")
    print(f" Alignment complete!")
    print(f" CSV ready for 4_mp4: {csv_path}")
    print("========================================\n")


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="One-shot WhisperX → CSV alignment pipeline"
    )
    parser.add_argument("slug", help="e.g. nirvana_come_as_you_are")
    args = parser.parse_args()

    run_alignment(args.slug)
