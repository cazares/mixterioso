#!/usr/bin/env python3
import argparse
import subprocess
from pathlib import Path
import whisperx
from faster_whisper import WhisperModel


# ------------------------------------------------------------
# Color constants
# ------------------------------------------------------------
RESET = "\033[0m"
BOLD  = "\033[1m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED   = "\033[31m"
BLUE  = "\033[34m"


# ------------------------------------------------------------
# Colorized subprocess runner
# ------------------------------------------------------------
def run_cmd(cmd):
    print(f"{BLUE}[CMD] {RESET}{' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Stream stdout
    for line in proc.stdout:
        print(f"{CYAN}[OUT]{RESET} {line.rstrip()}")

    # Stream stderr
    for line in proc.stderr:
        print(f"{YELLOW}[ERR]{RESET} {line.rstrip()}")

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"{RED}Subprocess failed with exit code {proc.returncode}{RESET}")


# ------------------------------------------------------------
# Convert MP3 → WAV (analysis only)
# ------------------------------------------------------------
def convert_mp3_to_wav(mp3_path: Path, wav_path: Path):
    wav_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}[FFMPEG]{RESET} Converting {mp3_path} → {wav_path}")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(mp3_path),
        "-ac", "1",
        "-ar", "16000",
        str(wav_path)
    ]

    run_cmd(cmd)


# ------------------------------------------------------------
# Convert aligned segments → 4-column CSV (start/end/text)
# ------------------------------------------------------------
def alignment_to_csv(aligned: dict, csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"{BLUE}[CSV]{RESET} Writing {csv_path}")

    with open(csv_path, "w") as f:
        f.write("line_index,start,end,text\n")

        for idx, seg in enumerate(aligned["segments"]):
            start = seg.get("start")
            end = seg.get("end")
            text = seg.get("text", "").replace('"', "'")

            if start is None or end is None:
                continue
            if not text:
                continue

            f.write(f"{idx},{start:.3f},{end:.3f},\"{text}\"\n")


# ------------------------------------------------------------
# Main forced alignment pipeline
# ------------------------------------------------------------
def run_alignment(slug: str):
    root = Path(".").resolve()

    mp3_path = root / "mp3s" / f"{slug}.mp3"
    txt_path = root / "txts" / f"{slug}.txt"
    wav_path = root / "wavs" / f"{slug}.wav"
    csv_path = root / "timings" / f"{slug}.csv"

    # --- Sanity checks ---
    if not mp3_path.exists():
        raise FileNotFoundError(f"{RED}Missing MP3:{RESET} {mp3_path}")
    if not txt_path.exists():
        raise FileNotFoundError(f"{RED}Missing TXT:{RESET} {txt_path}")

    # --- Step 1: Convert MP3 → WAV for alignment ---
    convert_mp3_to_wav(mp3_path, wav_path)

    # --- Step 2: Load lyrics (clean) ---
    lyric_lines = [
        line.strip()
        for line in txt_path.read_text().splitlines()
        if line.strip()
    ]

    # --- Step 3: ASR (NO VAD) ---
    print(f"{BLUE}[ASR]{RESET} Loading Whisper (medium, int8)…")
    device = "cpu"

    asr_model = WhisperModel(
        "medium",
        device=device,
        compute_type="int8"
    )

    print(f"{BLUE}[ASR]{RESET} Transcribing (bypassing VAD)…")

    segments, _ = asr_model.transcribe(str(wav_path))

    # Print ASR output colorized
    print(f"\n{GREEN}===== ASR OUTPUT ====={RESET}\n")

    whisper_result = {"segments": []}
    for i, seg in enumerate(segments):
        start = seg.start
        end = seg.end
        text = seg.text.strip()

        whisper_result["segments"].append({
            "text": text,
            "start": start,
            "end": end
        })

        print(
            f"{YELLOW}[SEG {i:03d}]{RESET} "
            f"{CYAN}{start:7.3f} → {end:7.3f}{RESET}  "
            f"{GREEN}{text}{RESET}"
        )

    print(f"\n{GREEN}===== END ASR OUTPUT ====={RESET}\n")

    # Load waveform for forced alignment
    print(f"{BLUE}[WhisperX]{RESET} Loading audio for alignment…")
    audio = whisperx.load_audio(str(wav_path))

    # --- Step 4: Alignment model (NO compute_type) ---
    print(f"{BLUE}[WhisperX]{RESET} Loading alignment model (Wav2Vec2)…")
    model_a, metadata = whisperx.load_align_model(
        language_code="en",
        device=device
    )

    # --- Step 5: Forced alignment ---
    print(f"{BLUE}[WhisperX]{RESET} Performing forced alignment…")

    aligned_asr = whisperx.align(
        whisper_result["segments"],
        model_a,
        metadata,
        audio,
        device
    )

    # --- Step 6: Replace ASR text with YOUR lyric lines ---
    print(f"{BLUE}[LYRICS]{RESET} Mapping lyrics to aligned segments…")

    segments = aligned_asr["segments"]
    out_segments = []

    min_len = min(len(lyric_lines), len(segments))

    for i in range(min_len):
        seg = segments[i]
        seg["text"] = lyric_lines[i]  # overwrite ASR text with real lyrics
        out_segments.append(seg)

    aligned = {"segments": out_segments}

    # --- Step 7: Save CSV for 4_mp4 ---
    alignment_to_csv(aligned, csv_path)

    print("\n========================================")
    print(f"{GREEN}Alignment complete!{RESET}")
    print(f"CSV ready for 4_mp4: {CYAN}{csv_path}{RESET}")
    print("========================================\n")


# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="One-shot WhisperX → CSV alignment pipeline (colorized, 4-column CSV)"
    )
    parser.add_argument("slug", help="e.g. nirvana_come_as_you_are")
    args = parser.parse_args()

    run_alignment(args.slug)
